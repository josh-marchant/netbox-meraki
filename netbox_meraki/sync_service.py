import logging
import re
import time
from dataclasses import dataclass
from ipaddress import ip_interface

from django.contrib.contenttypes.models import ContentType

from dcim.models import Device, DeviceRole, DeviceType, Interface, MACAddress, Manufacturer, Site
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomField, Tag
from ipam.models import Prefix, VLAN
from wireless.models import WirelessLAN

from .meraki_client import MerakiAPIClient
from .models import (
    MerakiBinding,
    MerakiVLANResolutionRule,
    PluginSettings,
    PrefixFilterRule,
    ReviewItem,
    SiteNameRule,
    SyncLog,
    SyncReview,
)

logger = logging.getLogger("netbox_meraki")
SSID_CAPABLE_PRODUCT_TYPES = {"wireless", "wirelesscontroller", "campusgateway"}
SWITCH_PORT_SWITCH_MODES = {"access", "trunk"}


def slugify_value(value, fallback="item"):
    slug = re.sub(r"[^a-z0-9-]+", "-", (value or "").lower()).strip("-")
    return slug or fallback


class SyncConflictError(Exception):
    pass


@dataclass
class VLANResolutionResult:
    status: str
    vlan: object = None
    group: object = None
    site: object = None
    source: str = ""
    detail: str = ""


@dataclass
class SwitchPortVLANResolution:
    untagged_vlan: object = None
    tagged_vlans: object = None
    apply_untagged: bool = False
    apply_tagged: bool = False

    def __post_init__(self):
        if self.tagged_vlans is None:
            self.tagged_vlans = []


class MerakiSyncService:
    def __init__(self, sync_mode="review", job=None, api_client=None):
        self.sync_mode = sync_mode
        self.job = job
        self._client = api_client
        self._vlan_rule_cache = None
        self.settings = PluginSettings.get_settings()
        self.sync_log = None
        self.review = None
        self.errors = []
        self.stats = {"organizations": 0, "networks": 0, "devices": 0, "vlans": 0, "prefixes": 0, "ssids": 0, "deleted_sites": 0, "deleted_devices": 0, "deleted_vlans": 0, "deleted_prefixes": 0}

    @property
    def client(self):
        if self._client is None:
            self._client = MerakiAPIClient()
        return self._client

    def ensure_custom_fields(self):
        specs = {
            Device: {
                "meraki_network_id": "Meraki Network ID",
                "meraki_firmware_version": "Meraki Firmware Version",
            },
            Interface: {
                "meraki_switch_port_mode": "Meraki Switch Port Mode",
                "meraki_allowed_vlans": "Meraki Allowed VLANs",
            },
            WirelessLAN: {
                "meraki_auth_mode": "Meraki Auth Mode",
                "meraki_encryption_mode": "Meraki Encryption Mode",
                "meraki_wpa_encryption_mode": "Meraki WPA Encryption Mode",
            },
        }
        for model, fields in specs.items():
            content_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
            for name, label in fields.items():
                custom_field, _ = CustomField.objects.get_or_create(
                    name=name,
                    defaults={"label": label, "type": CustomFieldTypeChoices.TYPE_TEXT, "group_name": "Meraki"},
                )
                custom_field.object_types.add(content_type)

    def sync_all(self, sync_log=None, organization_id=None, network_ids=None):
        started = time.monotonic()
        self.sync_log = sync_log or SyncLog.objects.create(status="queued", sync_mode=self.sync_mode)
        self.sync_log.status = "running"
        self.sync_log.message = "Synchronization started"
        self.sync_log.sync_mode = self.sync_mode
        self.sync_log.save(update_fields=["status", "message", "sync_mode"])
        if self.sync_mode in {"review", "dry_run"}:
            self.review, _ = SyncReview.objects.get_or_create(sync_log=self.sync_log)
            self.review.items.all().delete()
            self.review.status = "pending"
            self.review.reviewed = None
            self.review.reviewed_by = ""
            self.review.save(update_fields=["status", "reviewed", "reviewed_by"])
        try:
            self.ensure_custom_fields()
            organizations = self.client.get_organizations()
            if organization_id:
                organizations = [org for org in organizations if str(org.get("id")) == str(organization_id)]
            if not organizations:
                raise ValueError("No Meraki organizations matched the requested scope")
            for org in organizations:
                self._check_cancel()
                self._sync_org(org, network_ids)
                self.stats["organizations"] += 1
            full_sync = not organization_id and not network_ids
            if self.settings.enable_cleanup and full_sync:
                if self.sync_mode == "auto":
                    self._cleanup()
                else:
                    self._stage_cleanup()
            self._finish(time.monotonic() - started)
        except Exception as exc:
            logger.exception("Meraki sync failed")
            self.errors.append(str(exc))
            self.sync_log.status = "failed"
            self.sync_log.message = f"Synchronization failed: {exc}"
            self.sync_log.errors = self.errors
            self.sync_log.duration_seconds = time.monotonic() - started
            self.sync_log.save(update_fields=["status", "message", "errors", "duration_seconds"])
            raise
        return self.sync_log

    def _sync_org(self, org, network_ids=None):
        org_id = str(org.get("id"))
        networks = self.client.get_networks(org_id)
        if network_ids:
            allowed = {str(network_id) for network_id in network_ids}
            networks = [network for network in networks if str(network.get("id")) in allowed]
        inventory = self.client.get_inventory_devices(org_id)
        availability = {str(item.get("serial")): item for item in self.client.get_device_availabilities(org_id) if item.get("serial")}
        grouped = {}
        for item in inventory:
            network_id = str(item.get("networkId") or "")
            if network_id:
                grouped.setdefault(network_id, []).append(item)
        for network in networks:
            self._check_cancel()
            network_id = str(network.get("id"))
            network_name = network.get("name") or network_id
            site_name = SiteNameRule.transform_network_name(network_name)
            if site_name is None:
                continue
            site_name = self.settings.transform_name(site_name, self.settings.site_name_transform)
            site_data = {"name": site_name, "slug": slugify_value(site_name, f"site-{network_id.lower()}"), "description": network_name, "meraki_network_id": network_id}
            self._stage("site", network_id, site_name, site_data, self._find_site(network_id, site_name), {"meraki_organization_id": org_id, "meraki_network_id": network_id})
            for vlan_data in self.client.get_appliance_vlans(network_id):
                vlan_id = str(vlan_data.get("id") or "")
                if not vlan_id:
                    continue
                vlan_name = self.settings.transform_name(vlan_data.get("name") or f"VLAN {vlan_id}", self.settings.vlan_name_transform)
                vlan_key = f"{network_id}:{vlan_id}"
                site = Site.objects.filter(name=site_name).first()
                vlan_resolution = self._resolve_vlan(
                    site=site,
                    meraki_organization_id=org_id,
                    meraki_network_id=network_id,
                    vlan_id=int(vlan_data["id"]),
                    purpose="VLAN",
                    object_label=vlan_name,
                    allow_create_target=True,
                )
                if vlan_resolution.detail and vlan_resolution.status in {"ambiguous", "invalid"}:
                    self._record_sync_error(vlan_resolution.detail)
                vlan_payload = {
                    "site": site_name,
                    "vid": int(vlan_data["id"]),
                    "name": vlan_name,
                    "description": vlan_data.get("subnet", ""),
                    "network_id": network_id,
                    "organization_id": org_id,
                    "vlan_id": vlan_id,
                    "vlan_resolution": vlan_resolution.status,
                    "resolved_group_id": getattr(getattr(vlan_resolution, "group", None), "pk", None),
                    "resolved_site_id": getattr(getattr(vlan_resolution, "site", None), "pk", None),
                }
                self._stage("vlan", vlan_key, vlan_name, vlan_payload, self._find_vlan(vlan_key, site_name, int(vlan_data["id"])), {"meraki_organization_id": org_id, "meraki_network_id": network_id})
                subnet = vlan_data.get("subnet")
                if subnet and PrefixFilterRule.should_sync_prefix(subnet):
                    prefix_key = f"{network_id}:{subnet}"
                    prefix_payload = {
                        "site": site_name,
                        "prefix": subnet,
                        "description": vlan_data.get("name") or subnet,
                        "network_id": network_id,
                        "organization_id": org_id,
                        "vlan_vid": int(vlan_data["id"]),
                    }
                    self._stage("prefix", prefix_key, subnet, prefix_payload, self._find_prefix(prefix_key, subnet, site_name), {"meraki_organization_id": org_id, "meraki_network_id": network_id})
            for item in grouped.get(network_id, []):
                serial = str(item.get("serial") or "")
                if not serial:
                    continue
                detail = self.client.get_device(serial)
                payload = dict(item)
                payload.update(detail or {})
                payload.update(availability.get(serial, {}))
                payload["organizationId"] = org_id
                payload["networkId"] = network_id
                self._sync_device(site_name, payload)
            network_product_types = {str(product_type).lower() for product_type in (network.get("productTypes") or []) if product_type}
            if not network_product_types or network_product_types.intersection(SSID_CAPABLE_PRODUCT_TYPES):
                for ssid in self.client.get_wireless_ssids(network_id):
                    if not ssid.get("enabled", False) or ssid.get("number") is None:
                        continue
                    ssid_name = self.settings.transform_name(ssid.get("name") or f"SSID {ssid['number']}", self.settings.ssid_name_transform)
                    ssid_key = f"{network_id}:{ssid['number']}"
                    current_ssid = self._find_ssid(ssid_key, site_name, ssid_name)
                    ssid_payload = self._build_ssid_payload(org_id, network_id, site_name, ssid, current_ssid)
                    self._stage("ssid", ssid_key, ssid_name, ssid_payload, current_ssid, {"meraki_organization_id": org_id, "meraki_network_id": network_id, "meraki_ssid_number": int(ssid["number"])})
            self.stats["networks"] += 1

    def _sync_device(self, site_name, payload):
        serial = str(payload.get("serial") or "")
        if not serial:
            return
        product_type = payload.get("productType") or payload.get("productTypeName") or (payload.get("model") or "")[:2]
        name = self.settings.transform_name(payload.get("name") or serial, self.settings.device_name_transform)
        status = "offline" if (payload.get("status") or "").lower() in {"offline", "dormant"} else "active"
        data = {
            "name": name,
            "serial": serial,
            "site": site_name,
            "model": payload.get("model") or "Unknown",
            "manufacturer": "Cisco Meraki",
            "role": self.settings.get_device_role_for_product(product_type),
            "status": status,
            "notes": payload.get("notes") or "",
            "lan_ip": payload.get("lanIp") or "",
            "mac": payload.get("mac") or "",
            "meraki_network_id": str(payload.get("networkId") or ""),
            "firmware": payload.get("firmware") or "",
        }
        device = self._stage("device", serial, name, data, self._find_device(serial), {"meraki_organization_id": str(payload.get("organizationId") or ""), "meraki_network_id": str(payload.get("networkId") or ""), "meraki_serial": serial})
        if device and self.sync_mode == "auto" and self._supports_switch_ports(product_type):
            try:
                self._sync_switch_ports(
                    device,
                    serial,
                    site_name,
                    str(payload.get("organizationId") or ""),
                    str(payload.get("networkId") or ""),
                )
            except Exception as exc:
                logger.exception("Failed to sync switch ports for device %s", serial)
                self._record_sync_error(f"Failed to sync switch ports for device '{name}': {exc}")

    def _stage(self, item_type, identifier, object_name, data, current, binding_kwargs):
        conflict_message = self._detect_conflict(item_type, identifier, data, current)
        if conflict_message:
            self._record_sync_error(conflict_message)
            return current
        current_data = self._current(item_type, current)
        if current_data == self._normalize(item_type, data):
            binding = MerakiBinding.for_identifier(item_type, identifier)
            if binding:
                binding.touch(self.sync_log)
            elif current and self.sync_mode == "auto":
                MerakiBinding.bind(current, item_type, identifier, self.sync_log, **binding_kwargs)
            return current
        action_type = "update" if current else "create"
        if self.sync_mode == "auto":
            try:
                result = self._apply(item_type, identifier, data, binding_kwargs)
            except SyncConflictError as exc:
                self._record_sync_error(str(exc))
                return current
            except Exception as exc:
                logger.exception("Failed to apply %s %s", item_type, identifier)
                self._record_sync_error(f"Failed to {action_type} {item_type} '{object_name}': {exc}")
                return current
            if item_type == "device":
                self.stats["devices"] += 1
            elif item_type == "vlan":
                self.stats["vlans"] += 1
            elif item_type == "prefix":
                self.stats["prefixes"] += 1
            elif item_type == "ssid":
                self.stats["ssids"] += 1
            return result
        ReviewItem.objects.create(review=self.review, item_type=item_type, action_type=action_type, object_name=object_name, object_identifier=identifier, current_data=current_data, proposed_data=data, preview_display=f"{action_type.title()} {item_type}: {object_name}")
        return None

    def _apply(self, item_type, identifier, data, binding_kwargs):
        if item_type == "site":
            binding = MerakiBinding.for_identifier("site", identifier)
            obj = binding.bound_object if binding and binding.bound_object else self._find_site(identifier, data["name"]) or Site()
            obj.name = data["name"]
            obj.slug = data["slug"]
            obj.description = data.get("description", "")
            obj.save()
            self._tag(obj, "site")
        elif item_type == "vlan":
            binding = MerakiBinding.for_identifier("vlan", identifier)
            obj = binding.bound_object if binding and binding.bound_object else self._find_vlan(identifier, data["site"], data["vid"], data=data) or VLAN()
            target_site = Site.objects.filter(pk=data.get("resolved_site_id")).first() if data.get("resolved_site_id") else Site.objects.filter(name=data["site"]).first()
            if data.get("resolved_group_id"):
                obj.group_id = data["resolved_group_id"]
                obj.site = None
            else:
                obj.group = None
                obj.site = target_site
            obj.vid = data["vid"]
            obj.name = data["name"]
            obj.description = data.get("description", "")
            obj.status = "active"
            obj.save()
            self._tag(obj, "vlan")
        elif item_type == "prefix":
            site = Site.objects.get(name=data["site"])
            obj = self._find_prefix(identifier, data["prefix"], data["site"]) or Prefix(prefix=data["prefix"])
            obj.description = data.get("description", "")
            obj.status = "active"
            if data.get("vlan_vid"):
                prefix_vlan_resolution = self._resolve_vlan(
                    site=site,
                    meraki_organization_id=data.get("organization_id"),
                    meraki_network_id=data.get("network_id"),
                    vlan_id=data.get("vlan_vid"),
                    purpose="Prefix",
                    object_label=data["prefix"],
                )
                if prefix_vlan_resolution.status == "resolved":
                    obj.vlan = prefix_vlan_resolution.vlan
                elif prefix_vlan_resolution.detail:
                    self._record_sync_error(prefix_vlan_resolution.detail)
            if hasattr(obj, "scope_type"):
                obj.scope_type = ContentType.objects.get_for_model(site, for_concrete_model=False)
                obj.scope_id = site.pk
            elif hasattr(obj, "site"):
                obj.site = site
            obj.save()
            self._tag(obj, "prefix")
        elif item_type == "device":
            site = Site.objects.get(name=data["site"])
            manufacturer, _ = Manufacturer.objects.get_or_create(name=data["manufacturer"], defaults={"slug": "cisco-meraki"})
            device_type, _ = DeviceType.objects.get_or_create(model=data["model"], manufacturer=manufacturer, defaults={"slug": slugify_value(data["model"], f"device-{data['serial'].lower()}")})
            role = self._get_device_role(data["role"], data["name"])
            name = data["name"]
            if Device.objects.filter(name=name, site=site).exclude(serial=data["serial"]).exists():
                name = f"{name}-{data['serial'][-4:]}"
            obj = Device.objects.update_or_create(serial=data["serial"], defaults={"name": name, "device_type": device_type, "role": role, "site": site, "status": data["status"], "comments": data.get("notes", "")})[0]
            obj.custom_field_data.update({"meraki_network_id": data.get("meraki_network_id", ""), "meraki_firmware_version": data.get("firmware", "")})
            obj.save()
            self._tag(obj, "device")
            self._ensure_management(obj, data.get("lan_ip"), data.get("mac"), data.get("firmware"))
        elif item_type == "ssid":
            site = Site.objects.get(name=data["site"])
            obj = self._find_ssid(identifier, data["site"], data["ssid"]) or WirelessLAN(ssid=data["ssid"])
            obj.ssid = data["ssid"]
            obj.description = data.get("description", "")
            obj.status = "active"
            if hasattr(obj, "scope_type"):
                obj.scope_type = ContentType.objects.get_for_model(site, for_concrete_model=False)
                obj.scope_id = site.pk
            if hasattr(obj, "vlan_id") and data.get("vlan_resolution") == "resolved":
                resolution = self._resolve_vlan(
                    site=site,
                    meraki_organization_id=data.get("organization_id"),
                    meraki_network_id=data.get("network_id"),
                    vlan_id=data.get("vlan_vid"),
                    purpose="SSID",
                    object_label=data["ssid"],
                )
                if resolution.status == "resolved":
                    obj.vlan = resolution.vlan
            obj.save()
            obj.custom_field_data.update({"meraki_auth_mode": data.get("auth_mode", ""), "meraki_encryption_mode": data.get("encryption_mode", ""), "meraki_wpa_encryption_mode": data.get("wpa_encryption_mode", "")})
            obj.save()
        else:
            raise ValueError(f"Unsupported item type: {item_type}")
        MerakiBinding.bind(obj, item_type, identifier, self.sync_log, **binding_kwargs)
        return obj

    def apply_review_item(self, item):
        if item.action_type == "delete":
            binding = MerakiBinding.for_identifier(item.item_type, item.object_identifier)
            if binding and binding.bound_object:
                kind = binding.binding_kind
                binding.bound_object.delete()
                binding.delete()
                if kind == "site":
                    self.stats["deleted_sites"] += 1
                elif kind == "device":
                    self.stats["deleted_devices"] += 1
                elif kind == "vlan":
                    self.stats["deleted_vlans"] += 1
                elif kind == "prefix":
                    self.stats["deleted_prefixes"] += 1
            return
        final_data = item.get_final_data()
        binding_kwargs = {
            "meraki_organization_id": str(final_data.get("organization_id") or ""),
            "meraki_network_id": str(final_data.get("network_id") or final_data.get("meraki_network_id") or ""),
            "meraki_serial": str((final_data.get("serial") or item.object_identifier) if item.item_type == "device" else ""),
            "meraki_ssid_number": final_data.get("ssid_number") if item.item_type == "ssid" else None,
        }
        self._apply(item.item_type, item.object_identifier, final_data, binding_kwargs)

    def _record_sync_error(self, message):
        self.errors.append(message)
        if self.sync_log:
            self.sync_log.add_progress_log(message, level="error")

    def _binding_for_object(self, obj):
        if obj is None or getattr(obj, "pk", None) is None:
            return None
        content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        return MerakiBinding.objects.filter(object_type=content_type, object_id=obj.pk).first()

    def _get_unbound_candidate(self, queryset):
        for candidate in queryset:
            if self._binding_for_object(candidate) is None:
                return candidate
        return None

    def _site_prefix_queryset(self, site, prefix):
        queryset = Prefix.objects.filter(prefix=prefix)
        if hasattr(Prefix, "scope_type"):
            content_type = ContentType.objects.get_for_model(site, for_concrete_model=False)
            queryset = queryset.filter(scope_type=content_type, scope_id=site.pk)
        return queryset

    def _site_ssid_queryset(self, site, ssid_name):
        queryset = WirelessLAN.objects.filter(ssid=ssid_name)
        if hasattr(WirelessLAN, "scope_type"):
            content_type = ContentType.objects.get_for_model(site, for_concrete_model=False)
            queryset = queryset.filter(scope_type=content_type, scope_id=site.pk)
        return queryset

    def _build_ssid_payload(self, organization_id, network_id, site_name, ssid, current=None):
        ssid_name = self.settings.transform_name(ssid.get("name") or f"SSID {ssid['number']}", self.settings.ssid_name_transform)
        resolved_vlan_vid, resolution = self._resolve_meraki_ssid_vlan(network_id, ssid)
        site = Site.objects.filter(name=site_name).first()

        if resolution == "resolved":
            resolved_vlan = self._resolve_vlan(
                site=site,
                meraki_organization_id=organization_id,
                meraki_network_id=network_id,
                vlan_id=resolved_vlan_vid,
                purpose="SSID",
                object_label=ssid_name,
            )
            if resolved_vlan.status == "resolved":
                vlan_vid = resolved_vlan.vlan.vid
            else:
                resolution = resolved_vlan.status
                vlan_vid = self._current_ssid_vlan_vid(current)
                self._record_sync_error(resolved_vlan.detail)
        else:
            vlan_vid = self._current_ssid_vlan_vid(current)

        return {
            "site": site_name,
            "ssid": ssid_name,
            "ssid_number": int(ssid["number"]),
            "description": f"Meraki SSID #{ssid['number']}",
            "organization_id": organization_id,
            "network_id": network_id,
            "auth_mode": ssid.get("authMode") or "",
            "encryption_mode": ssid.get("encryptionMode") or "",
            "wpa_encryption_mode": ssid.get("wpaEncryptionMode") or "",
            "vlan_vid": vlan_vid,
            "vlan_resolution": resolution,
        }

    def _resolve_meraki_ssid_vlan(self, network_id, ssid):
        resolved_vlan_vid, resolution = self._extract_ssid_vlan_vid(ssid)
        if resolution != "needs_detail":
            return resolved_vlan_vid, resolution

        detail = self.client.get_wireless_ssid(network_id, ssid["number"]) or {}
        return self._extract_ssid_vlan_vid(detail)

    def _extract_ssid_vlan_vid(self, ssid):
        ip_assignment_mode = str(ssid.get("ipAssignmentMode") or "").strip().lower()

        vlan_id = ssid.get("vlanId")
        if self._is_valid_vlan_vid(vlan_id):
            return int(vlan_id), "resolved"

        default_vlan_id = ssid.get("defaultVlanId")
        ap_tag_mappings = ssid.get("apTagsAndVlanIds") or []
        if self._is_valid_vlan_vid(default_vlan_id) and not ap_tag_mappings:
            return int(default_vlan_id), "resolved"

        if self._contains_ssid_vlan_data(ssid):
            return None, "unresolved"

        if ip_assignment_mode in {"nat mode", "ethernet over gre", "campus gateway"}:
            return None, "unresolved"

        return None, "needs_detail"

    def _contains_ssid_vlan_data(self, ssid):
        if any(key in ssid for key in ("vlanId", "defaultVlanId", "apTagsAndVlanIds")):
            return True
        named_vlans = ssid.get("namedVlans")
        if not isinstance(named_vlans, dict):
            return False
        tagging = named_vlans.get("tagging")
        radius = named_vlans.get("radius")
        return isinstance(tagging, dict) or isinstance(radius, dict)

    def _is_valid_vlan_vid(self, vlan_id):
        try:
            return 1 <= int(vlan_id) <= 4094
        except (TypeError, ValueError):
            return False

    def _current_ssid_vlan_vid(self, current):
        if current is None:
            return None
        vlan = getattr(current, "vlan", None)
        return getattr(vlan, "vid", None)

    def _iter_matching_vlan_rules(self, *, site=None, meraki_organization_id="", meraki_network_id=""):
        if self._vlan_rule_cache is None:
            self._vlan_rule_cache = list(
                MerakiVLANResolutionRule.objects.filter(enabled=True).select_related("site", "vlan_group")
            )
        rules = self._vlan_rule_cache
        matching = [
            rule for rule in rules
            if rule.matches(
                meraki_organization_id=meraki_organization_id,
                meraki_network_id=meraki_network_id,
                site=site,
            )
        ]
        return sorted(matching, key=lambda rule: (rule.match_scope_rank, rule.priority, rule.name.lower(), rule.pk))

    def _resolve_vlan(self, *, site, meraki_organization_id="", meraki_network_id="", vlan_id=None, purpose="", object_label="", allow_create_target=False):
        purpose_label = purpose or "Object"
        object_name = object_label or str(meraki_network_id or meraki_organization_id or site or "unknown")
        if vlan_id in (None, ""):
            return VLANResolutionResult(status="unsupported", detail="")
        try:
            resolved_vid = int(vlan_id)
        except (TypeError, ValueError):
            return VLANResolutionResult(
                status="invalid",
                detail=f"Failed to sync {purpose_label} '{object_name}': invalid VLAN '{vlan_id}'.",
            )

        for rule in self._iter_matching_vlan_rules(
            site=site,
            meraki_organization_id=meraki_organization_id,
            meraki_network_id=meraki_network_id,
        ):
            matches = list(VLAN.objects.filter(group=rule.vlan_group, vid=resolved_vid))
            if len(matches) == 1:
                return VLANResolutionResult(
                    status="resolved",
                    vlan=matches[0],
                    group=rule.vlan_group,
                    source=f"rule:{rule.name}",
                    detail=f"Resolved via VLAN resolution rule '{rule.name}'.",
                )
            if len(matches) > 1:
                return VLANResolutionResult(
                    status="ambiguous",
                    source=f"rule:{rule.name}",
                    detail=(
                        f"Failed to sync {purpose_label} '{object_name}': VLAN {resolved_vid} matched multiple VLANs "
                        f"in VLAN group '{rule.vlan_group}'."
                    ),
                )
            if allow_create_target:
                return VLANResolutionResult(
                    status="resolved",
                    group=rule.vlan_group,
                    source=f"rule:{rule.name}",
                    detail=f"Resolved creation target via VLAN resolution rule '{rule.name}'.",
                )
            return VLANResolutionResult(
                status="missing",
                group=rule.vlan_group,
                source=f"rule:{rule.name}",
                detail=(
                    f"Failed to sync {purpose_label} '{object_name}': VLAN {resolved_vid} was not found in "
                    f"VLAN group '{rule.vlan_group}'."
                ),
            )

        if site is not None:
            site_matches = list(VLAN.objects.filter(site=site, vid=resolved_vid))
            if len(site_matches) == 1:
                return VLANResolutionResult(
                    status="resolved",
                    vlan=site_matches[0],
                    site=site,
                    source="site",
                    detail=f"Resolved via legacy site-scoped VLAN lookup for site '{site.name}'.",
                )
            if len(site_matches) > 1:
                return VLANResolutionResult(
                    status="ambiguous",
                    source="site",
                    detail=(
                        f"Failed to sync {purpose_label} '{object_name}': VLAN {resolved_vid} matched multiple "
                        f"site-scoped VLANs for site '{site.name}'."
                    ),
                )
            if allow_create_target:
                return VLANResolutionResult(
                    status="resolved",
                    site=site,
                    source="legacy-site",
                    detail=f"Resolved creation target via legacy site-scoped VLAN lookup for site '{site.name}'.",
                )

        global_matches = list(VLAN.objects.filter(vid=resolved_vid))
        if len(global_matches) == 1:
            return VLANResolutionResult(
                status="resolved",
                vlan=global_matches[0],
                source="global",
                detail=f"Resolved via unique global VLAN lookup for VLAN {resolved_vid}.",
            )
        if len(global_matches) > 1:
            return VLANResolutionResult(
                status="ambiguous",
                source="global",
                detail=(
                    f"Failed to sync {purpose_label} '{object_name}': VLAN {resolved_vid} matched multiple NetBox VLANs "
                    "and no VLAN resolution rule applied."
                ),
            )
        return VLANResolutionResult(
            status="missing",
            source="none",
            detail=(
                f"Failed to sync {purpose_label} '{object_name}': VLAN {resolved_vid} was not found in NetBox and "
                "no VLAN resolution rule applied."
            ),
        )

    def _get_conflicting_binding(self, item_type, identifier, data, current):
        if item_type == "site":
            candidates = Site.objects.filter(name=data["name"])
        elif item_type == "vlan":
            site = Site.objects.filter(name=data["site"]).first()
            candidates = VLAN.objects.filter(site=site, vid=data["vid"]) if site else VLAN.objects.none()
        elif item_type == "prefix":
            site = Site.objects.filter(name=data["site"]).first()
            candidates = self._site_prefix_queryset(site, data["prefix"]) if site else Prefix.objects.none()
        elif item_type == "ssid":
            site = Site.objects.filter(name=data["site"]).first()
            candidates = self._site_ssid_queryset(site, data["ssid"]) if site else WirelessLAN.objects.none()
        else:
            return None

        for candidate in candidates:
            if current is not None and candidate.pk == current.pk:
                continue
            binding = self._binding_for_object(candidate)
            if binding and (binding.binding_kind != item_type or binding.meraki_identifier != identifier):
                return binding
        return None

    def _detect_conflict(self, item_type, identifier, data, current):
        binding = self._get_conflicting_binding(item_type, identifier, data, current)
        if binding and binding.bound_object:
            return (
                f"Skipped {item_type} '{data.get('name') or data.get('ssid') or data.get('prefix') or identifier}' "
                f"because {binding.bound_object} is already bound to Meraki identifier {binding.meraki_identifier}."
            )
        return None

    def _ensure_management(self, device, lan_ip, mac, firmware):
        if not lan_ip and not mac:
            return
        interface = self._ensure_management_interface(device, firmware)
        self._reconcile_management_mac(device, interface, mac)
        self._reconcile_management_ip(device, interface, lan_ip)

    def _get_device_role(self, role_name, device_name):
        role = DeviceRole.objects.filter(name=role_name).first()
        if role is not None:
            return role
        if not self.settings.auto_create_device_roles:
            raise SyncConflictError(
                f"Skipped device '{device_name}': device role '{role_name}' does not exist and "
                "automatic role creation is disabled."
            )
        role = DeviceRole(name=role_name, slug=slugify_value(role_name, "meraki-role"), color="607d8b")
        role.save()
        return role

    def _supports_switch_ports(self, product_type):
        normalized = str(product_type or "").strip().lower().replace("_", "").replace("-", "")
        return normalized in {"ms", "switch"}

    def _sync_switch_ports(self, device, serial, site_name, meraki_organization_id="", meraki_network_id=""):
        site = Site.objects.filter(name=site_name).first()
        for port in self.client.get_switch_ports(serial):
            port_id = str(port.get("portId") or "")
            if not port_id:
                continue
            meraki_port_type = str(port.get("type") or "").strip().lower()
            description = port.get("name") or f"Meraki switch port {port_id}"
            interface, _ = Interface.objects.get_or_create(
                device=device,
                name=port_id,
                defaults={
                    "type": "other",
                    "enabled": bool(port.get("enabled", True)),
                    "description": description,
                },
            )
            update_fields = []
            if interface.enabled != bool(port.get("enabled", True)):
                interface.enabled = bool(port.get("enabled", True))
                update_fields.append("enabled")
            if interface.description != description:
                interface.description = description
                update_fields.append("description")

            raw_allowed_vlans = str(port.get("allowedVlans") or "").strip()
            custom_field_data = dict(getattr(interface, "custom_field_data", {}) or {})
            if custom_field_data.get("meraki_switch_port_mode", "") != meraki_port_type:
                custom_field_data["meraki_switch_port_mode"] = meraki_port_type
                update_fields.append("custom_field_data")
            if custom_field_data.get("meraki_allowed_vlans", "") != raw_allowed_vlans:
                custom_field_data["meraki_allowed_vlans"] = raw_allowed_vlans
                update_fields.append("custom_field_data")
            if "custom_field_data" in update_fields:
                interface.custom_field_data = custom_field_data

            expected_mode = self._expected_switch_port_mode(meraki_port_type, raw_allowed_vlans)
            current_mode = getattr(interface, "mode", "") or ""
            if current_mode != expected_mode:
                interface.mode = expected_mode
                update_fields.append("mode")

            vlan_resolution = self._resolve_switch_port_vlans(
                site=site,
                meraki_organization_id=meraki_organization_id,
                meraki_network_id=meraki_network_id,
                serial=serial,
                port_id=port_id,
                meraki_port_type=meraki_port_type,
                raw_allowed_vlans=raw_allowed_vlans,
                native_vlan=port.get("vlan"),
            )
            if vlan_resolution.apply_untagged and getattr(interface, "untagged_vlan_id", None) != getattr(vlan_resolution.untagged_vlan, "pk", None):
                interface.untagged_vlan = vlan_resolution.untagged_vlan
                update_fields.append("untagged_vlan")

            if update_fields:
                interface.save(update_fields=list(dict.fromkeys(update_fields)))

            if hasattr(interface, "tagged_vlans") and vlan_resolution.apply_tagged:
                current_tagged_ids = list(interface.tagged_vlans.order_by("pk").values_list("pk", flat=True))
                expected_tagged_ids = sorted(vlan.pk for vlan in vlan_resolution.tagged_vlans)
                if current_tagged_ids != expected_tagged_ids:
                    interface.tagged_vlans.set(vlan_resolution.tagged_vlans)

    def _log_progress(self, message, level="info"):
        if self.sync_log:
            self.sync_log.add_progress_log(message, level=level)

    def _ensure_management_interface(self, device, firmware):
        description = f"Meraki management interface ({firmware})" if firmware else "Meraki management interface"
        interface, _ = Interface.objects.get_or_create(
            device=device,
            name="Management",
            defaults={
                "type": "other",
                "enabled": True,
                "description": description,
            },
        )
        update_fields = []
        if interface.description != description:
            interface.description = description
            update_fields.append("description")
        if interface.enabled is not True:
            interface.enabled = True
            update_fields.append("enabled")
        if hasattr(interface, "mgmt_only") and getattr(interface, "mgmt_only") is not True:
            interface.mgmt_only = True
            update_fields.append("mgmt_only")
        if update_fields:
            interface.save(update_fields=update_fields)
        return interface

    def _assigned_object_label(self, assigned_object):
        if assigned_object is None:
            return "an unassigned object"
        if hasattr(assigned_object, "device") and hasattr(assigned_object, "name"):
            return f"interface '{assigned_object.device}:{assigned_object.name}'"
        if hasattr(assigned_object, "name"):
            return f"{assigned_object._meta.verbose_name} '{assigned_object.name}'"
        return str(assigned_object)

    def _normalize_management_address(self, lan_ip):
        raw_value = str(lan_ip or "").strip()
        if not raw_value:
            return None
        try:
            parsed = ip_interface(raw_value if "/" in raw_value else f"{raw_value}/32")
        except ValueError:
            self._record_sync_error(f"Management IP skipped due to conflict: '{raw_value}' is not a valid IPv4 address.")
            return None
        if parsed.version != 4:
            self._record_sync_error(f"Management IP skipped due to conflict: '{raw_value}' is not an IPv4 address.")
            return None
        return f"{parsed.ip}/32"

    def _reconcile_management_mac(self, device, interface, mac):
        mac_value = str(mac or "").strip()
        if not mac_value:
            return

        matches = list(MACAddress.objects.filter(mac_address=mac_value))
        if len(matches) > 1:
            self._record_sync_error(
                f"Management MAC skipped due to conflict for device '{device.name}': {mac_value} exists multiple times."
            )
            return

        mac_record = matches[0] if matches else None
        action = ""
        if mac_record is None:
            mac_record = MACAddress(mac_address=mac_value)
            mac_record.assigned_object = interface
            mac_record.save()
            action = "created"
        else:
            assigned_object = getattr(mac_record, "assigned_object", None)
            if assigned_object == interface:
                action = "reused existing"
            elif assigned_object is None:
                primary_owner = self._primary_mac_owner_label(mac_record, interface)
                if primary_owner:
                    self._record_sync_error(
                        f"Management MAC skipped due to conflict for device '{device.name}': {mac_value} "
                        f"is still the primary MAC for {primary_owner}."
                    )
                    return
                mac_record.assigned_object = interface
                mac_record.save()
                action = "reused unassigned"
            else:
                self._record_sync_error(
                    f"Management MAC skipped due to conflict for device '{device.name}': {mac_value} is already assigned to {self._assigned_object_label(assigned_object)}."
                )
                return

        if getattr(interface, "primary_mac_address_id", None) != mac_record.pk and getattr(mac_record, "assigned_object", None) == interface:
            interface.primary_mac_address = mac_record
            interface.save(update_fields=["primary_mac_address"])
        self._log_progress(f"Management MAC {action} for device '{device.name}': {mac_value}")

    def _reconcile_management_ip(self, device, interface, lan_ip):
        normalized_address = self._normalize_management_address(lan_ip)
        if not normalized_address:
            return

        from ipam.models import IPAddress

        host_address = normalized_address.split("/")[0]
        exact_matches = list(IPAddress.objects.filter(address=normalized_address))
        if len(exact_matches) > 1:
            self._record_sync_error(
                f"Management IP skipped due to conflict for device '{device.name}': {normalized_address} exists multiple times."
            )
            return

        ip_record = exact_matches[0] if exact_matches else None
        action = ""
        if ip_record is not None:
            if getattr(ip_record, "vrf_id", None) is not None:
                self._record_sync_error(
                    f"Management IP skipped due to conflict for device '{device.name}': {normalized_address} exists in a VRF."
                )
                return
            assigned_object = getattr(ip_record, "assigned_object", None)
            if assigned_object == interface:
                update_fields = []
                if getattr(ip_record, "status", None) != "active":
                    ip_record.status = "active"
                    update_fields.append("status")
                expected_description = f"Management IP for {device.name}"
                if getattr(ip_record, "description", "") != expected_description:
                    ip_record.description = expected_description
                    update_fields.append("description")
                if update_fields:
                    ip_record.save(update_fields=update_fields)
                action = "reused existing"
            elif assigned_object is None:
                primary_owner = self._primary_ip_owner_label(ip_record, device)
                if primary_owner:
                    self._record_sync_error(
                        f"Management IP skipped due to conflict for device '{device.name}': {normalized_address} "
                        f"is still the primary IP for {primary_owner}."
                    )
                    return
                ip_record.status = "active"
                ip_record.description = f"Management IP for {device.name}"
                ip_record.assigned_object = interface
                ip_record.save()
                action = "reused unassigned"
            else:
                self._record_sync_error(
                    f"Management IP skipped due to conflict for device '{device.name}': {normalized_address} is already assigned to {self._assigned_object_label(assigned_object)}."
                )
                return
        else:
            same_host_conflict = self._global_same_host_conflict(IPAddress, normalized_address, host_address)
            if same_host_conflict:
                self._record_sync_error(
                    f"Management IP skipped due to conflict for device '{device.name}': host {host_address} "
                    f"already exists globally as {same_host_conflict}."
                )
                return
            ip_record = IPAddress(
                address=normalized_address,
                status="active",
                description=f"Management IP for {device.name}",
            )
            ip_record.assigned_object = interface
            ip_record.save()
            action = "created"

        if getattr(ip_record, "assigned_object", None) == interface and getattr(device, "primary_ip4_id", None) != ip_record.pk:
            device.primary_ip4 = ip_record
            device.save(update_fields=["primary_ip4"])
        if getattr(ip_record, "assigned_object", None) == interface:
            self._log_progress(f"Management IP {action} for device '{device.name}': {normalized_address}")

    def _primary_mac_owner_label(self, mac_record, interface):
        owner = (
            Interface.objects.filter(primary_mac_address=mac_record)
            .exclude(pk=getattr(interface, "pk", None))
            .select_related("device")
            .first()
        )
        if owner is None:
            return None
        return f"interface '{owner.device}:{owner.name}'"

    def _primary_ip_owner_label(self, ip_record, device):
        owner = (
            Device.objects.filter(primary_ip4=ip_record)
            .exclude(pk=getattr(device, "pk", None))
            .only("name")
            .first()
        )
        if owner is not None:
            return f"device '{owner.name}'"

        try:
            from virtualization.models import VirtualMachine
        except Exception:
            VirtualMachine = None

        if VirtualMachine is None:
            return None

        vm = VirtualMachine.objects.filter(primary_ip4=ip_record).only("name").first()
        if vm is not None:
            return f"virtual machine '{vm.name}'"
        return None

    def _global_same_host_conflict(self, ip_model, normalized_address, host_address):
        for candidate in ip_model.objects.filter(vrf__isnull=True):
            candidate_address = str(getattr(candidate, "address", "") or "")
            if not candidate_address or candidate_address == normalized_address:
                continue
            try:
                parsed_candidate = ip_interface(candidate_address)
            except ValueError:
                continue
            if parsed_candidate.version != 4:
                continue
            if str(parsed_candidate.ip) != host_address:
                continue
            if parsed_candidate.network.prefixlen == 32:
                continue
            return candidate_address
        return None

    def _expected_switch_port_mode(self, meraki_port_type, raw_allowed_vlans):
        if meraki_port_type == "access":
            return "access"
        if meraki_port_type == "trunk":
            if str(raw_allowed_vlans or "").strip().lower() == "all":
                tagged_all_mode = self._tagged_all_mode_value()
                if tagged_all_mode:
                    return tagged_all_mode
            return "tagged"
        return ""

    def _tagged_all_mode_value(self):
        field = Interface._meta.get_field("mode")
        for value, _label in getattr(field, "choices", []):
            if str(value).strip().lower().replace("_", "-") == "tagged-all":
                return value
        return None

    def _resolve_switch_port_vlans(self, *, site, meraki_organization_id, meraki_network_id, serial, port_id, meraki_port_type, raw_allowed_vlans, native_vlan):
        if meraki_port_type not in SWITCH_PORT_SWITCH_MODES:
            return SwitchPortVLANResolution()

        resolution = SwitchPortVLANResolution()
        native_result = self._resolve_vlan(
            site=site,
            meraki_organization_id=meraki_organization_id,
            meraki_network_id=meraki_network_id,
            vlan_id=native_vlan,
            purpose="switch port",
            object_label=f"{serial} port {port_id}",
        )
        if native_result.status == "resolved":
            resolution.untagged_vlan = native_result.vlan
            resolution.apply_untagged = True
        elif native_result.detail:
            self._record_sync_error(native_result.detail)

        if meraki_port_type == "access":
            resolution.apply_tagged = True
            return resolution

        allowed_vlans = str(raw_allowed_vlans or "").strip()
        if allowed_vlans.lower() == "all":
            resolution.apply_tagged = True
            return resolution

        vlan_ids, invalid_tokens = self._parse_allowed_vlans(allowed_vlans)
        if invalid_tokens:
            self._record_sync_error(
                f"Failed to sync switch ports for device '{serial}' port '{port_id}': invalid allowed VLAN tokens {', '.join(invalid_tokens)}."
            )

        tagged_vlans = []
        seen_vlan_ids = set()
        all_resolved = not invalid_tokens
        for vlan_id in vlan_ids:
            if vlan_id in seen_vlan_ids:
                continue
            seen_vlan_ids.add(vlan_id)
            tagged_result = self._resolve_vlan(
                site=site,
                meraki_organization_id=meraki_organization_id,
                meraki_network_id=meraki_network_id,
                vlan_id=vlan_id,
                purpose="switch port",
                object_label=f"{serial} port {port_id}",
            )
            if tagged_result.status == "resolved":
                tagged_vlans.append(tagged_result.vlan)
            else:
                all_resolved = False
                if tagged_result.detail:
                    self._record_sync_error(tagged_result.detail)
        if all_resolved:
            resolution.tagged_vlans = tagged_vlans
            resolution.apply_tagged = True
        return resolution

    def _parse_allowed_vlans(self, raw_allowed_vlans):
        vlan_ids = []
        invalid_tokens = []
        for token in [item.strip() for item in str(raw_allowed_vlans or "").split(",") if item.strip()]:
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                try:
                    start = int(start_text)
                    end = int(end_text)
                except (TypeError, ValueError):
                    invalid_tokens.append(token)
                    continue
                if end < start:
                    invalid_tokens.append(token)
                    continue
                vlan_ids.extend(range(start, end + 1))
                continue
            try:
                vlan_ids.append(int(token))
            except (TypeError, ValueError):
                invalid_tokens.append(token)
        return vlan_ids, invalid_tokens

    def _cleanup(self):
        for binding in MerakiBinding.objects.exclude(last_seen_sync=self.sync_log):
            if binding.bound_object:
                kind = binding.binding_kind
                binding.bound_object.delete()
                if kind == "site":
                    self.stats["deleted_sites"] += 1
                elif kind == "device":
                    self.stats["deleted_devices"] += 1
                elif kind == "vlan":
                    self.stats["deleted_vlans"] += 1
                elif kind == "prefix":
                    self.stats["deleted_prefixes"] += 1
            binding.delete()

    def _stage_cleanup(self):
        if not self.review:
            return
        for binding in MerakiBinding.objects.exclude(last_seen_sync=self.sync_log):
            if binding.bound_object:
                ReviewItem.objects.create(review=self.review, item_type=binding.binding_kind, action_type="delete", object_name=str(binding.bound_object), object_identifier=binding.meraki_identifier, current_data={"object": str(binding.bound_object)}, proposed_data={}, preview_display=f"Delete {binding.binding_kind}: {binding.bound_object}")

    def _find_site(self, identifier, name):
        binding = MerakiBinding.for_identifier("site", identifier)
        if binding and binding.bound_object:
            binding.touch(self.sync_log)
            return binding.bound_object
        return self._get_unbound_candidate(Site.objects.filter(name=name))

    def _find_device(self, serial):
        binding = MerakiBinding.for_identifier("device", serial)
        if binding and binding.bound_object:
            binding.touch(self.sync_log)
            return binding.bound_object
        return Device.objects.filter(serial=serial).first()

    def _find_vlan(self, identifier, site_name, vid, data=None):
        binding = MerakiBinding.for_identifier("vlan", identifier)
        if binding and binding.bound_object:
            binding.touch(self.sync_log)
            return binding.bound_object
        if data:
            if data.get("resolved_group_id"):
                return self._get_unbound_candidate(VLAN.objects.filter(group_id=data["resolved_group_id"], vid=vid))
            if data.get("resolved_site_id"):
                return self._get_unbound_candidate(VLAN.objects.filter(site_id=data["resolved_site_id"], vid=vid))
        site = Site.objects.filter(name=site_name).first()
        return self._get_unbound_candidate(VLAN.objects.filter(site=site, vid=vid)) if site else None

    def _find_prefix(self, identifier, prefix, site_name):
        binding = MerakiBinding.for_identifier("prefix", identifier)
        if binding and binding.bound_object:
            binding.touch(self.sync_log)
            return binding.bound_object
        site = Site.objects.filter(name=site_name).first()
        if not site:
            return None
        return self._get_unbound_candidate(self._site_prefix_queryset(site, prefix))

    def _find_ssid(self, identifier, site_name, ssid_name):
        if identifier:
            binding = MerakiBinding.for_identifier("ssid", identifier)
            if binding and binding.bound_object:
                binding.touch(self.sync_log)
                return binding.bound_object
        site = Site.objects.filter(name=site_name).first()
        if not site:
            return None
        return self._get_unbound_candidate(self._site_ssid_queryset(site, ssid_name))

    def _current(self, item_type, obj):
        if not obj:
            return None
        if item_type == "site":
            return {"name": obj.name, "slug": obj.slug, "description": obj.description}
        if item_type == "device":
            management = obj.interfaces.filter(name="Management").first()
            primary_ip = str(getattr(getattr(obj, "primary_ip4", None), "address", "") or "")
            if "/" in primary_ip:
                primary_ip = primary_ip.split("/")[0]
            mac_value = ""
            if management is not None:
                primary_mac = getattr(management, "primary_mac_address", None)
                mac_value = str(getattr(primary_mac, "mac_address", "") or getattr(management, "mac_address", "") or "")
            return {"name": obj.name, "serial": obj.serial, "site": obj.site.name, "model": obj.device_type.model, "manufacturer": obj.device_type.manufacturer.name, "role": obj.role.name, "status": obj.status, "meraki_network_id": obj.custom_field_data.get("meraki_network_id", ""), "firmware": obj.custom_field_data.get("meraki_firmware_version", ""), "lan_ip": primary_ip, "mac": mac_value}
        if item_type == "vlan":
            return {
                "site": obj.site.name if obj.site else "",
                "vid": obj.vid,
                "name": obj.name,
                "description": obj.description,
                "resolved_group_id": getattr(obj, "group_id", None),
                "resolved_site_id": getattr(obj, "site_id", None),
            }
        if item_type == "prefix":
            return {
                "prefix": str(obj.prefix),
                "description": obj.description,
                "vlan_vid": getattr(getattr(obj, "vlan", None), "vid", None),
            }
        if item_type == "ssid":
            return {
                "ssid": obj.ssid,
                "description": obj.description,
                "auth_mode": obj.custom_field_data.get("meraki_auth_mode", ""),
                "encryption_mode": obj.custom_field_data.get("meraki_encryption_mode", ""),
                "wpa_encryption_mode": obj.custom_field_data.get("meraki_wpa_encryption_mode", ""),
                "vlan_vid": getattr(getattr(obj, "vlan", None), "vid", None),
            }
        return None

    def _normalize(self, item_type, data):
        if item_type == "site":
            return {k: data.get(k) for k in ("name", "slug", "description")}
        if item_type == "device":
            return {k: data.get(k) for k in ("name", "serial", "site", "model", "manufacturer", "role", "status", "meraki_network_id", "firmware", "lan_ip", "mac")}
        if item_type == "vlan":
            return {k: data.get(k) for k in ("site", "vid", "name", "description", "resolved_group_id", "resolved_site_id")}
        if item_type == "prefix":
            return {k: data.get(k) for k in ("prefix", "description", "vlan_vid")}
        if item_type == "ssid":
            return {k: data.get(k) for k in ("ssid", "description", "auth_mode", "encryption_mode", "wpa_encryption_mode", "vlan_vid")}
        return dict(data)

    def _tag(self, obj, object_type):
        for tag_name in self.settings.get_tags_for_object_type(object_type):
            tag, _ = Tag.objects.get_or_create(name=tag_name, defaults={"slug": slugify_value(tag_name, "meraki")})
            obj.tags.add(tag)

    def _check_cancel(self):
        if self.sync_log and self.sync_log.check_cancel_requested():
            self.sync_log.status = "cancelled"
            self.sync_log.message = "Sync cancelled by user"
            self.sync_log.save(update_fields=["status", "message"])
            raise RuntimeError("Sync cancelled by user")

    def _finish(self, duration_seconds):
        self.sync_log.organizations_synced = self.stats["organizations"]
        self.sync_log.networks_synced = self.stats["networks"]
        self.sync_log.devices_synced = self.stats["devices"]
        self.sync_log.vlans_synced = self.stats["vlans"]
        self.sync_log.prefixes_synced = self.stats["prefixes"]
        self.sync_log.ssids_synced = self.stats["ssids"]
        self.sync_log.deleted_sites = self.stats["deleted_sites"]
        self.sync_log.deleted_devices = self.stats["deleted_devices"]
        self.sync_log.deleted_vlans = self.stats["deleted_vlans"]
        self.sync_log.deleted_prefixes = self.stats["deleted_prefixes"]
        self.sync_log.errors = self.errors
        self.sync_log.duration_seconds = duration_seconds
        if self.sync_mode == "dry_run":
            self.sync_log.status = "dry_run"
            self.sync_log.message = f"Dry run completed with {self.review.items.count() if self.review else 0} staged changes"
        elif self.sync_mode == "review":
            total = self.review.items.count() if self.review else 0
            self.review.items_total = total
            self.review.items_approved = self.review.items.filter(status="approved").count()
            self.review.items_rejected = self.review.items.filter(status="rejected").count()
            self.review.status = "pending"
            self.review.save(update_fields=["items_total", "items_approved", "items_rejected", "status"])
            self.sync_log.status = "pending_review" if total else "success"
            self.sync_log.message = f"Review ready with {total} staged changes" if total else "No changes detected"
        elif self.errors:
            self.sync_log.status = "partial"
            self.sync_log.message = f"Sync completed with {len(self.errors)} error(s)"
        else:
            self.sync_log.status = "success"
            self.sync_log.message = "Sync completed successfully"
        self.sync_log.progress_percent = 100
        self.sync_log.current_operation = "Completed"
        self.sync_log.save()
