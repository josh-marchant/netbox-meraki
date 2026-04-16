import base64
import hashlib
import logging
import re
from ipaddress import ip_network

from django.conf import settings as django_settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from cryptography.fernet import Fernet, InvalidToken

from ipam.models import VLANGroup
from dcim.models import Site

logger = logging.getLogger(__name__)


SYNC_MODE_CHOICES = [
    ("auto", "Auto Sync"),
    ("review", "Sync with Review"),
    ("dry_run", "Dry Run Only"),
]


class PluginSettings(models.Model):
    meraki_base_url = models.CharField(
        max_length=255,
        default="https://api.meraki.com/api/v1",
        verbose_name="Meraki API Base URL",
        help_text="Base URL for the Meraki Dashboard API.",
    )
    meraki_api_key_encrypted = models.TextField(
        blank=True,
        default="",
        verbose_name="Encrypted Meraki API Key",
    )
    mx_device_role = models.CharField(
        max_length=100,
        default="Meraki Firewall",
        verbose_name="MX Device Role",
        help_text="Device role for MX and appliance devices.",
    )
    ms_device_role = models.CharField(
        max_length=100,
        default="Meraki Switch",
        verbose_name="MS Device Role",
        help_text="Device role for MS and switch devices.",
    )
    mr_device_role = models.CharField(
        max_length=100,
        default="Meraki AP",
        verbose_name="MR Device Role",
        help_text="Device role for MR and wireless devices.",
    )
    mg_device_role = models.CharField(
        max_length=100,
        default="Meraki Cellular Gateway",
        verbose_name="MG Device Role",
        help_text="Device role for MG and cellular gateway devices.",
    )
    mv_device_role = models.CharField(
        max_length=100,
        default="Meraki Camera",
        verbose_name="MV Device Role",
        help_text="Device role for MV and camera devices.",
    )
    mt_device_role = models.CharField(
        max_length=100,
        default="Meraki Sensor",
        verbose_name="MT Device Role",
        help_text="Device role for MT and sensor devices.",
    )
    default_device_role = models.CharField(
        max_length=100,
        default="Meraki Unknown",
        verbose_name="Default Device Role",
        help_text="Fallback device role for unsupported Meraki product types.",
    )
    auto_create_device_roles = models.BooleanField(
        default=True,
        help_text="Automatically create device roles if they do not exist.",
    )
    sync_mode = models.CharField(
        max_length=20,
        choices=SYNC_MODE_CHOICES,
        default="review",
        verbose_name="Default Sync Mode",
    )
    device_name_transform = models.CharField(
        max_length=20,
        choices=[
            ("keep", "Keep Original"),
            ("upper", "UPPERCASE"),
            ("lower", "lowercase"),
            ("title", "Title Case"),
        ],
        default="keep",
        verbose_name="Device Name Transform",
    )
    site_name_transform = models.CharField(
        max_length=20,
        choices=[
            ("keep", "Keep Original"),
            ("upper", "UPPERCASE"),
            ("lower", "lowercase"),
            ("title", "Title Case"),
        ],
        default="keep",
        verbose_name="Site Name Transform",
    )
    vlan_name_transform = models.CharField(
        max_length=20,
        choices=[
            ("keep", "Keep Original"),
            ("upper", "UPPERCASE"),
            ("lower", "lowercase"),
            ("title", "Title Case"),
        ],
        default="keep",
        verbose_name="VLAN Name Transform",
    )
    ssid_name_transform = models.CharField(
        max_length=20,
        choices=[
            ("keep", "Keep Original"),
            ("upper", "UPPERCASE"),
            ("lower", "lowercase"),
            ("title", "Title Case"),
        ],
        default="keep",
        verbose_name="SSID Name Transform",
    )
    site_tags = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Site Tags",
        help_text="Optional comma-separated tags to apply to synced sites.",
    )
    device_tags = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Device Tags",
        help_text="Optional comma-separated tags to apply to synced devices.",
    )
    vlan_tags = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="VLAN Tags",
        help_text="Optional comma-separated tags to apply to synced VLANs.",
    )
    prefix_tags = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Prefix Tags",
        help_text="Optional comma-separated tags to apply to synced prefixes.",
    )
    process_unmatched_sites = models.BooleanField(
        default=True,
        verbose_name="Process Sites Not Matching Name Rules",
        help_text="If disabled, only networks matching a site naming rule are synced.",
    )
    enable_api_throttling = models.BooleanField(
        default=True,
        verbose_name="Enable API Throttling",
        help_text="Rate limit Meraki API requests using the configured requests-per-second value.",
    )
    api_requests_per_second = models.IntegerField(
        default=5,
        verbose_name="API Requests Per Second",
        help_text="Maximum Meraki API requests per second. Meraki's documented limit is 10/sec.",
    )
    enable_cleanup = models.BooleanField(
        default=False,
        verbose_name="Enable Cleanup",
        help_text="Delete previously bound NetBox objects that are absent from a full Meraki sync.",
    )
    enable_multithreading = models.BooleanField(
        default=False,
        verbose_name="Enable Multithreading (Deprecated)",
        help_text="Deprecated. This setting is ignored by the hardened fork.",
    )
    max_worker_threads = models.IntegerField(
        default=3,
        verbose_name="Max Worker Threads (Deprecated)",
        help_text="Deprecated. This setting is ignored by the hardened fork.",
    )

    class Meta:
        verbose_name = "Plugin Settings"
        verbose_name_plural = "Plugin Settings"

    def __str__(self):
        return "Meraki Plugin Settings"

    def clean(self):
        super().clean()
        if self.api_requests_per_second < 1 or self.api_requests_per_second > 10:
            raise ValidationError(
                {"api_requests_per_second": "API requests per second must be between 1 and 10."}
            )
        from .meraki_client import MerakiAPIClient

        try:
            self.meraki_base_url = MerakiAPIClient.validate_base_url(self.meraki_base_url)
        except ValueError as exc:
            raise ValidationError({"meraki_base_url": str(exc)})

    @property
    def has_meraki_api_key(self):
        return bool(self.meraki_api_key_encrypted)

    @staticmethod
    def _build_fernet():
        secret_key = getattr(django_settings, "SECRET_KEY", "")
        digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def set_meraki_api_key(self, api_key):
        api_key = (api_key or "").strip()
        if not api_key:
            self.meraki_api_key_encrypted = ""
            return
        self.meraki_api_key_encrypted = self._build_fernet().encrypt(api_key.encode("utf-8")).decode("utf-8")

    def get_meraki_api_key(self):
        if not self.meraki_api_key_encrypted:
            return ""
        try:
            return self._build_fernet().decrypt(self.meraki_api_key_encrypted.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ValidationError("Stored Meraki API key could not be decrypted.") from exc

    def clear_meraki_api_key(self):
        self.meraki_api_key_encrypted = ""

    def get_tags_for_object_type(self, object_type):
        tag_field_map = {
            "site": self.site_tags,
            "device": self.device_tags,
            "vlan": self.vlan_tags,
            "prefix": self.prefix_tags,
        }
        raw_value = tag_field_map.get(object_type, "")
        return [tag.strip() for tag in raw_value.split(",") if tag.strip()]

    def transform_name(self, name, transform_type):
        if not name:
            return name
        if transform_type == "upper":
            return name.upper()
        if transform_type == "lower":
            return name.lower()
        if transform_type == "title":
            return name.title()
        return name

    @classmethod
    def get_settings(cls):
        settings_instance, _ = cls.objects.get_or_create(pk=1)
        return settings_instance

    def get_device_role_for_product(self, product_type):
        if not product_type:
            return self.default_device_role

        normalized = product_type.lower()
        mapping = {
            "mx": self.mx_device_role,
            "appliance": self.mx_device_role,
            "ms": self.ms_device_role,
            "switch": self.ms_device_role,
            "mr": self.mr_device_role,
            "wireless": self.mr_device_role,
            "wirelesscontroller": self.mr_device_role,
            "wireless_controller": self.mr_device_role,
            "mg": self.mg_device_role,
            "cellulargateway": self.mg_device_role,
            "cellular_gateway": self.mg_device_role,
            "mv": self.mv_device_role,
            "camera": self.mv_device_role,
            "mt": self.mt_device_role,
            "sensor": self.mt_device_role,
        }
        compact = normalized.replace("_", "").replace("-", "")
        return mapping.get(normalized) or mapping.get(compact) or mapping.get(normalized[:2]) or self.default_device_role


class SiteNameRule(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="Descriptive name for this rule.")
    regex_pattern = models.CharField(
        max_length=500,
        verbose_name="Regex Pattern",
        help_text="Regular expression to match Meraki network names.",
    )
    site_name_template = models.CharField(
        max_length=200,
        verbose_name="Site Name Template",
        help_text="Use named groups like {site}, positional groups like {0}, or {network_name}.",
    )
    priority = models.IntegerField(default=100, help_text="Lower values run first.")
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["priority", "name"]
        verbose_name = "Site Name Rule"
        verbose_name_plural = "Site Name Rules"

    def __str__(self):
        return f"{self.name} (Priority: {self.priority})"

    def clean(self):
        super().clean()
        try:
            re.compile(self.regex_pattern)
        except re.error as exc:
            raise ValidationError({"regex_pattern": f"Invalid regular expression: {exc}"})

    def apply(self, network_name):
        if not self.enabled:
            return network_name
        match = re.match(self.regex_pattern, network_name)
        if not match:
            return network_name

        value = self.site_name_template.replace("{network_name}", network_name)
        for name, captured in match.groupdict().items():
            value = value.replace(f"{{{name}}}", captured or "")
        for index, captured in enumerate(match.groups()):
            value = value.replace(f"{{{index}}}", captured or "")
        return value.strip()

    @classmethod
    def transform_network_name(cls, network_name):
        for rule in cls.objects.filter(enabled=True).order_by("priority"):
            transformed = rule.apply(network_name)
            if transformed != network_name:
                return transformed
        settings = PluginSettings.get_settings()
        if settings.process_unmatched_sites:
            return network_name
        return None


class PrefixFilterRule(models.Model):
    FILTER_TYPE_CHOICES = [
        ("exclude", "Exclude Matching Prefixes"),
        ("include_only", "Include Only Matching Prefixes"),
    ]
    PREFIX_LENGTH_CHOICES = [
        ("exact", "Exact Length"),
        ("greater", "Greater Than"),
        ("less", "Less Than"),
        ("range", "Range"),
    ]

    name = models.CharField(max_length=100, unique=True, help_text="Descriptive name for this filter rule.")
    filter_type = models.CharField(max_length=20, choices=FILTER_TYPE_CHOICES, default="exclude")
    prefix_pattern = models.CharField(max_length=200, blank=True, verbose_name="Prefix Pattern")
    prefix_length_filter = models.CharField(max_length=20, choices=PREFIX_LENGTH_CHOICES, default="exact")
    min_prefix_length = models.IntegerField(null=True, blank=True)
    max_prefix_length = models.IntegerField(null=True, blank=True)
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["priority", "name"]
        verbose_name = "Prefix Filter Rule"
        verbose_name_plural = "Prefix Filter Rules"

    def __str__(self):
        return f"{self.name} ({self.get_filter_type_display()}, Priority: {self.priority})"

    def clean(self):
        super().clean()
        if self.prefix_pattern:
            try:
                ip_network(self.prefix_pattern, strict=False)
            except ValueError as exc:
                raise ValidationError({"prefix_pattern": f"Invalid prefix pattern: {exc}"})

        if self.prefix_length_filter in {"greater", "less", "range"} and self.min_prefix_length is None:
            raise ValidationError({"min_prefix_length": "Minimum prefix length is required for this filter type."})

        for field_name in ("min_prefix_length", "max_prefix_length"):
            value = getattr(self, field_name)
            if value is not None and (value < 1 or value > 128):
                raise ValidationError({field_name: "Prefix length must be between 1 and 128."})

        if (
            self.prefix_length_filter == "range"
            and self.min_prefix_length is not None
            and self.max_prefix_length is not None
            and self.min_prefix_length > self.max_prefix_length
        ):
            raise ValidationError({"max_prefix_length": "Maximum prefix length must be greater than or equal to minimum."})

    def matches(self, prefix_str):
        if not self.enabled:
            return False
        try:
            prefix = ip_network(prefix_str, strict=False)
        except ValueError:
            return False

        if self.prefix_pattern:
            pattern = ip_network(self.prefix_pattern, strict=False)
            if not (prefix.subnet_of(pattern) or prefix == pattern):
                return False

        if self.prefix_length_filter == "exact" and self.min_prefix_length is not None:
            return prefix.prefixlen == self.min_prefix_length
        if self.prefix_length_filter == "greater" and self.min_prefix_length is not None:
            return prefix.prefixlen > self.min_prefix_length
        if self.prefix_length_filter == "less" and self.min_prefix_length is not None:
            return prefix.prefixlen < self.min_prefix_length
        if self.prefix_length_filter == "range":
            if self.min_prefix_length is not None and prefix.prefixlen < self.min_prefix_length:
                return False
            if self.max_prefix_length is not None and prefix.prefixlen > self.max_prefix_length:
                return False
        return True

    @classmethod
    def should_sync_prefix(cls, prefix_str):
        rules = cls.objects.filter(enabled=True).order_by("priority")
        if not rules.exists():
            return True

        for rule in rules.filter(filter_type="exclude"):
            if rule.matches(prefix_str):
                return False

        include_only_rules = rules.filter(filter_type="include_only")
        if include_only_rules.exists():
            return any(rule.matches(prefix_str) for rule in include_only_rules)
        return True


class MerakiVLANResolutionRule(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="Descriptive name for this resolution rule.")
    priority = models.IntegerField(default=100, help_text="Lower values run first within the same match scope.")
    enabled = models.BooleanField(default=True)
    meraki_organization_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Meraki Organization ID",
        help_text="Optional Meraki organization ID to match.",
    )
    meraki_network_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Meraki Network ID",
        help_text="Optional Meraki network ID to match.",
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="+",
        help_text="Optional mapped NetBox site to match.",
    )
    vlan_group = models.ForeignKey(
        VLANGroup,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Target NetBox VLAN group used to resolve matching VLAN IDs.",
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["priority", "name"]
        verbose_name = "VLAN Resolution Rule"
        verbose_name_plural = "VLAN Resolution Rules"

    def __str__(self):
        return f"{self.name} -> {self.vlan_group}"

    def clean(self):
        super().clean()
        if not any((self.meraki_organization_id.strip(), self.meraki_network_id.strip(), self.site_id)):
            raise ValidationError(
                "At least one match criterion is required: Meraki organization, Meraki network, or NetBox site."
            )

    def matches(self, *, meraki_organization_id="", meraki_network_id="", site=None):
        if not self.enabled:
            return False
        if self.meraki_network_id and self.meraki_network_id != str(meraki_network_id or ""):
            return False
        if self.meraki_organization_id and self.meraki_organization_id != str(meraki_organization_id or ""):
            return False
        if self.site_id and getattr(site, "pk", None) != self.site_id:
            return False
        return True

    @property
    def match_scope_rank(self):
        if self.meraki_network_id:
            return 0
        if self.meraki_organization_id and self.site_id:
            return 1
        if self.meraki_organization_id:
            return 2
        if self.site_id:
            return 3
        return 4


class SyncLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("queued", "Queued"),
            ("success", "Success"),
            ("partial", "Partial Success"),
            ("failed", "Failed"),
            ("running", "Running"),
            ("dry_run", "Dry Run"),
            ("pending_review", "Pending Review"),
            ("cancelled", "Cancelled"),
        ],
        default="queued",
    )
    message = models.TextField(blank=True)
    organizations_synced = models.IntegerField(default=0)
    networks_synced = models.IntegerField(default=0)
    devices_synced = models.IntegerField(default=0)
    vlans_synced = models.IntegerField(default=0)
    prefixes_synced = models.IntegerField(default=0)
    ssids_synced = models.IntegerField(default=0)
    deleted_sites = models.IntegerField(default=0)
    deleted_devices = models.IntegerField(default=0)
    deleted_vlans = models.IntegerField(default=0)
    deleted_prefixes = models.IntegerField(default=0)
    updated_prefixes = models.IntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    progress_logs = models.JSONField(default=list, blank=True)
    current_operation = models.CharField(max_length=255, blank=True)
    progress_percent = models.IntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    sync_mode = models.CharField(max_length=20, default="auto", choices=SYNC_MODE_CHOICES)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Sync Log"
        verbose_name_plural = "Sync Logs"
        permissions = (
            ("run_sync", "Can queue Meraki sync jobs"),
            ("cancel_sync", "Can cancel Meraki sync jobs"),
        )

    def __str__(self):
        return f"Sync {self.timestamp:%Y-%m-%d %H:%M:%S} - {self.status}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_meraki:synclog", args=[self.pk])

    def add_progress_log(self, message, level="info"):
        entry = {
            "timestamp": timezone.now().isoformat(),
            "level": level,
            "message": message,
        }
        progress_logs = list(self.progress_logs or [])
        progress_logs.append(entry)
        self.progress_logs = progress_logs
        self.save(update_fields=["progress_logs"])

    def update_progress(self, operation, percent):
        self.current_operation = operation
        self.progress_percent = min(100, max(0, percent))
        self.save(update_fields=["current_operation", "progress_percent"])

    def request_cancel(self):
        self.cancel_requested = True
        self.cancelled_at = timezone.now()
        self.save(update_fields=["cancel_requested", "cancelled_at"])

    def check_cancel_requested(self):
        self.refresh_from_db(fields=["cancel_requested"])
        return self.cancel_requested


class SyncReview(models.Model):
    sync_log = models.OneToOneField(SyncLog, on_delete=models.CASCADE, related_name="review")
    created = models.DateTimeField(auto_now_add=True)
    reviewed = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending Review"),
            ("approved", "Approved"),
            ("partially_approved", "Partially Approved"),
            ("rejected", "Rejected"),
            ("applied", "Applied"),
        ],
        default="pending",
    )
    items_total = models.IntegerField(default=0)
    items_approved = models.IntegerField(default=0)
    items_rejected = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Sync Review"
        verbose_name_plural = "Sync Reviews"
        permissions = (("review_sync", "Can review and apply Meraki sync changes"),)

    def __str__(self):
        return f"Review for Sync {self.sync_log_id} - {self.status}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_meraki:review_detail", args=[self.pk])

    def mark_reviewed(self, user, status):
        self.reviewed = timezone.now()
        self.reviewed_by = getattr(user, "username", str(user))
        self.status = status
        self.items_total = self.items.count()
        self.items_approved = self.items.filter(status="approved").count()
        self.items_rejected = self.items.filter(status="rejected").count()
        self.save(
            update_fields=[
                "reviewed",
                "reviewed_by",
                "status",
                "items_total",
                "items_approved",
                "items_rejected",
            ]
        )

    def calculate_status(self):
        counts = {
            "pending": self.items.filter(status="pending").count(),
            "approved": self.items.filter(status="approved").count(),
            "rejected": self.items.filter(status="rejected").count(),
            "applied": self.items.filter(status="applied").count(),
            "failed": self.items.filter(status="failed").count(),
        }
        if counts["applied"] and not counts["pending"] and not counts["approved"] and not counts["failed"]:
            return "applied"
        if counts["approved"] and not counts["pending"] and not counts["rejected"] and not counts["failed"]:
            return "approved"
        if counts["rejected"] and not counts["pending"] and not counts["approved"] and not counts["applied"]:
            return "rejected"
        if any(counts.values()):
            return "partially_approved"
        return "pending"

    def apply_approved_items(self, user=None):
        from .sync_service import MerakiSyncService

        service = MerakiSyncService(sync_mode="auto")
        for item_type in ("site", "vlan", "prefix", "device", "ssid"):
            for item in self.items.filter(status="approved", item_type=item_type).order_by("id"):
                try:
                    service.apply_review_item(item)
                    item.status = "applied"
                    item.error_message = ""
                    item.save(update_fields=["status", "error_message"])
                except Exception as exc:
                    item.status = "failed"
                    item.error_message = str(exc)
                    item.save(update_fields=["status", "error_message"])
                    logger.exception("Failed to apply %s review item %s", item_type, item.pk)

        self.mark_reviewed(user or self.reviewed_by or "system", self.calculate_status())


class ReviewItem(models.Model):
    ITEM_TYPES = [
        ("site", "Site"),
        ("device", "Device"),
        ("device_type", "Device Type"),
        ("vlan", "VLAN"),
        ("prefix", "Prefix"),
        ("interface", "Interface"),
        ("ip_address", "IP Address"),
        ("ssid", "SSID"),
    ]
    ACTION_TYPES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
        ("skip", "Skip"),
    ]

    review = models.ForeignKey(SyncReview, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(max_length=20, choices=ITEM_TYPES)
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    object_name = models.CharField(max_length=255)
    object_identifier = models.CharField(max_length=255)
    current_data = models.JSONField(null=True, blank=True)
    proposed_data = models.JSONField(default=dict, blank=True)
    editable_data = models.JSONField(null=True, blank=True)
    preview_display = models.TextField(blank=True)
    related_object_info = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("applied", "Applied"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    error_message = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["item_type", "object_name"]
        verbose_name = "Review Item"
        verbose_name_plural = "Review Items"

    def __str__(self):
        return f"{self.action_type} {self.item_type}: {self.object_name}"

    def get_final_data(self):
        return self.editable_data if self.editable_data is not None else self.proposed_data

    def get_changes(self):
        if self.action_type in {"create", "delete"} or not self.current_data:
            return self.get_final_data()
        changes = {}
        final_data = self.get_final_data()
        for key, new_value in final_data.items():
            old_value = self.current_data.get(key)
            if old_value != new_value:
                changes[key] = {"old": old_value, "new": new_value}
        return changes


class MerakiBinding(models.Model):
    BINDING_KIND_CHOICES = [
        ("site", "Site"),
        ("device", "Device"),
        ("vlan", "VLAN"),
        ("prefix", "Prefix"),
        ("ssid", "SSID"),
    ]

    binding_kind = models.CharField(max_length=20, choices=BINDING_KIND_CHOICES)
    object_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    bound_object = GenericForeignKey("object_type", "object_id")
    meraki_identifier = models.CharField(max_length=255)
    meraki_organization_id = models.CharField(max_length=64, blank=True)
    meraki_network_id = models.CharField(max_length=64, blank=True)
    meraki_serial = models.CharField(max_length=64, blank=True)
    meraki_ssid_number = models.PositiveIntegerField(null=True, blank=True)
    last_seen_sync = models.ForeignKey(SyncLog, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["binding_kind", "meraki_identifier"]
        constraints = (
            models.UniqueConstraint(fields=("binding_kind", "meraki_identifier"), name="netbox_meraki_unique_binding_identifier"),
            models.UniqueConstraint(fields=("object_type", "object_id"), name="netbox_meraki_unique_bound_object"),
        )

    def __str__(self):
        return f"{self.binding_kind}:{self.meraki_identifier}"

    @classmethod
    def bind(
        cls,
        bound_object,
        binding_kind,
        meraki_identifier,
        sync_log=None,
        meraki_organization_id="",
        meraki_network_id="",
        meraki_serial="",
        meraki_ssid_number=None,
    ):
        content_type = ContentType.objects.get_for_model(bound_object, for_concrete_model=False)
        defaults = {
            "binding_kind": binding_kind,
            "meraki_identifier": meraki_identifier,
            "meraki_organization_id": meraki_organization_id or "",
            "meraki_network_id": meraki_network_id or "",
            "meraki_serial": meraki_serial or "",
            "meraki_ssid_number": meraki_ssid_number,
            "last_seen_sync": sync_log,
        }
        with transaction.atomic():
            identifier_binding = (
                cls.objects.select_for_update()
                .filter(binding_kind=binding_kind, meraki_identifier=meraki_identifier)
                .first()
            )
            object_binding = (
                cls.objects.select_for_update()
                .filter(object_type=content_type, object_id=bound_object.pk)
                .first()
            )

            if identifier_binding:
                if object_binding and object_binding.pk != identifier_binding.pk:
                    object_binding.delete()
                for field, value in defaults.items():
                    setattr(identifier_binding, field, value)
                identifier_binding.object_type = content_type
                identifier_binding.object_id = bound_object.pk
                identifier_binding.save()
                return identifier_binding

            if object_binding:
                for field, value in defaults.items():
                    setattr(object_binding, field, value)
                object_binding.save()
                return object_binding

            return cls.objects.create(object_type=content_type, object_id=bound_object.pk, **defaults)

    @classmethod
    def for_identifier(cls, binding_kind, meraki_identifier):
        return cls.objects.select_related("object_type", "last_seen_sync").filter(
            binding_kind=binding_kind,
            meraki_identifier=meraki_identifier,
        ).first()

    def touch(self, sync_log):
        self.last_seen_sync = sync_log
        self.save(update_fields=["last_seen_sync", "updated"])


class ScheduledJobTracker(models.Model):
    netbox_job_id = models.IntegerField(
        unique=True,
        verbose_name="NetBox Job ID",
        help_text="Legacy tracker field retained for compatibility. New scheduling metadata lives in Job.data.",
    )
    job_name = models.CharField(max_length=200, verbose_name="Job Name")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Created")

    class Meta:
        verbose_name = "Scheduled Job Tracker"
        verbose_name_plural = "Scheduled Job Trackers"
        ordering = ["-created"]

    def __str__(self):
        return f"{self.job_name} (ID: {self.netbox_job_id})"


class MerakiSchedule(models.Model):
    name = models.CharField(max_length=200)
    sync_mode = models.CharField(max_length=20, choices=SYNC_MODE_CHOICES, default="review")
    organization_id = models.CharField(max_length=64, blank=True)
    network_ids = models.JSONField(default=list, blank=True)
    run_at = models.DateTimeField()
    interval_minutes = models.PositiveIntegerField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    current_job = models.ForeignKey(
        "core.Job",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    last_job = models.ForeignKey(
        "core.Job",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "Meraki Schedule"
        verbose_name_plural = "Meraki Schedules"

    def __str__(self):
        return self.name

    @property
    def has_network_scope(self):
        return bool(self.network_ids)
