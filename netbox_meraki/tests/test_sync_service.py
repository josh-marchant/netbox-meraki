from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from netbox_meraki.sync_service import MerakiSyncService


def _settings_stub():
    return SimpleNamespace(
        transform_name=lambda name, transform: name,
        ssid_name_transform="keep",
    )


class MerakiSyncServiceSSIDVlanTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("netbox_meraki.sync_service.PluginSettings.get_settings", return_value=_settings_stub())
        self.addCleanup(patcher.stop)
        patcher.start()
        self.service = MerakiSyncService(sync_mode="auto", api_client=Mock())

    def test_extract_ssid_vlan_vid_prefers_vlan_id(self):
        vlan_vid, resolution = self.service._extract_ssid_vlan_vid(
            {"ipAssignmentMode": "VPN", "vlanId": 30}
        )
        self.assertEqual((vlan_vid, resolution), (30, "resolved"))

    def test_extract_ssid_vlan_vid_uses_default_vlan_id_when_no_ap_tag_overrides(self):
        vlan_vid, resolution = self.service._extract_ssid_vlan_vid(
            {"ipAssignmentMode": "Bridge mode", "defaultVlanId": 20}
        )
        self.assertEqual((vlan_vid, resolution), (20, "resolved"))

    def test_extract_ssid_vlan_vid_marks_tag_based_vlan_assignment_unresolved(self):
        vlan_vid, resolution = self.service._extract_ssid_vlan_vid(
            {
                "ipAssignmentMode": "Bridge mode",
                "defaultVlanId": 20,
                "apTagsAndVlanIds": [{"tags": ["lobby"], "vlanId": 30}],
            }
        )
        self.assertEqual((vlan_vid, resolution), (None, "unresolved"))

    def test_extract_ssid_vlan_vid_requests_detail_when_vlan_fields_missing(self):
        vlan_vid, resolution = self.service._extract_ssid_vlan_vid(
            {"ipAssignmentMode": "Bridge mode"}
        )
        self.assertEqual((vlan_vid, resolution), (None, "needs_detail"))

    def test_resolve_meraki_ssid_vlan_uses_detail_endpoint_when_needed(self):
        self.service.client.get_wireless_ssid.return_value = {"defaultVlanId": 55}
        vlan_vid, resolution = self.service._resolve_meraki_ssid_vlan(
            "N_123",
            {"number": 7, "ipAssignmentMode": "Bridge mode"},
        )
        self.assertEqual((vlan_vid, resolution), (55, "resolved"))
        self.service.client.get_wireless_ssid.assert_called_once_with("N_123", 7)

    @patch("netbox_meraki.sync_service.Site.objects.filter")
    def test_build_ssid_payload_preserves_existing_vlan_when_unresolved(self, site_filter):
        site_filter.return_value.first.return_value = None
        current = SimpleNamespace(vlan=SimpleNamespace(vid=40))

        with patch.object(self.service, "_resolve_meraki_ssid_vlan", return_value=(None, "unresolved")):
            payload = self.service._build_ssid_payload(
                "1234",
                "N_123",
                "MySite",
                {"number": 3, "name": "Corp", "authMode": "open"},
                current=current,
            )

        self.assertEqual(payload["vlan_vid"], 40)
        self.assertEqual(payload["vlan_resolution"], "unresolved")

    @patch("netbox_meraki.sync_service.Site.objects.filter")
    def test_build_ssid_payload_preserves_existing_vlan_when_resolved_vlan_missing(self, site_filter):
        site_filter.return_value.first.return_value = SimpleNamespace(name="MySite")
        current = SimpleNamespace(vlan=SimpleNamespace(vid=40))

        with patch.object(self.service, "_resolve_meraki_ssid_vlan", return_value=(50, "resolved")):
            with patch.object(
                self.service,
                "_resolve_vlan",
                return_value=SimpleNamespace(status="missing", detail="missing"),
            ):
                payload = self.service._build_ssid_payload(
                    "1234",
                    "N_123",
                    "MySite",
                    {"number": 3, "name": "Corp", "authMode": "open"},
                    current=current,
                )

        self.assertEqual(payload["vlan_vid"], 40)
        self.assertEqual(payload["vlan_resolution"], "missing")

    def test_normalize_ssid_includes_vlan_vid(self):
        normalized = self.service._normalize(
            "ssid",
            {
                "ssid": "Corp",
                "description": "Meraki SSID #1",
                "auth_mode": "open",
                "encryption_mode": "",
                "wpa_encryption_mode": "",
                "vlan_vid": 99,
            },
        )
        self.assertEqual(normalized["vlan_vid"], 99)


class MerakiSyncServiceManagementIPTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("netbox_meraki.sync_service.PluginSettings.get_settings", return_value=_settings_stub())
        self.addCleanup(patcher.stop)
        patcher.start()
        self.service = MerakiSyncService(sync_mode="auto", api_client=Mock())
        self.errors = []
        self.progress = []
        self.service._record_sync_error = self.errors.append
        self.service._log_progress = lambda message, level="info": self.progress.append((message, level))

    def test_resolves_management_mask_from_matching_appliance_vlan(self):
        normalized = self.service._normalize_management_address(
            "10.0.0.1",
            {"network_vlans": [{"applianceIp": "10.0.0.1", "subnet": "10.0.0.0/24"}]},
        )
        self.assertEqual(normalized, "10.0.0.1/24")

    def test_resolves_management_mask_from_cellular_gateway_context(self):
        normalized = self.service._normalize_management_address(
            "192.168.0.33",
            {
                "direct_ip": "192.168.0.33",
                "direct_subnet": "192.168.0.32/27",
                "direct_source": "cellular gateway LAN settings",
            },
        )
        self.assertEqual(normalized, "192.168.0.33/27")

    def test_resolves_management_mask_from_management_interface_context(self):
        normalized = self.service._normalize_management_address(
            "172.16.0.10",
            {
                "direct_ip": "172.16.0.10",
                "direct_subnet": "172.16.0.10/255.255.255.0",
                "direct_source": "management interface settings",
            },
        )
        self.assertEqual(normalized, "172.16.0.10/24")

    def test_prefers_mask_present_in_raw_lan_ip(self):
        normalized = self.service._normalize_management_address("10.10.10.5/28", {})
        self.assertEqual(normalized, "10.10.10.5/28")

    def test_falls_back_to_host_route_when_no_context_matches(self):
        normalized = self.service._normalize_management_address("10.10.10.5", {})
        self.assertEqual(normalized, "10.10.10.5/32")

    def test_rejects_invalid_ipv4_management_ip(self):
        normalized = self.service._normalize_management_address("not-an-ip", {})
        self.assertIsNone(normalized)
        self.assertEqual(len(self.errors), 1)

    def test_refuses_subnet_that_does_not_contain_host(self):
        normalized = self.service._normalize_management_address(
            "10.0.0.10",
            {
                "direct_ip": "10.0.0.10",
                "direct_subnet": "10.0.1.0/24",
                "direct_source": "cellular gateway LAN settings",
            },
        )
        self.assertEqual(normalized, "10.0.0.10/32")
        self.assertTrue(any("not inside subnet" in message for message in self.errors))

    def test_extract_management_interface_subnet_matches_static_ip(self):
        subnet = self.service._extract_management_interface_subnet(
            {"wan1": {"staticIp": "192.0.2.10", "staticSubnetMask": "255.255.255.0"}},
            "192.0.2.10",
        )
        self.assertEqual(subnet, "192.0.2.10/255.255.255.0")

    def test_extract_management_interface_subnet_ignores_non_matching_static_ip(self):
        subnet = self.service._extract_management_interface_subnet(
            {"wan1": {"staticIp": "192.0.2.11", "staticSubnetMask": "255.255.255.0"}},
            "192.0.2.10",
        )
        self.assertIsNone(subnet)

    def test_build_management_context_uses_cellular_gateway_data_for_mg_devices(self):
        self.service.client.get_device_cellular_gateway_lan.return_value = {
            "deviceLanIp": "192.168.0.33",
            "deviceSubnet": "192.168.0.32/27",
        }
        self.service.client.get_device_management_interface.return_value = {}

        context = self.service._build_management_context(
            {"serial": "Q2XX-1234", "productType": "mg", "lanIp": "192.168.0.33"}
        )

        self.assertEqual(context["direct_ip"], "192.168.0.33")
        self.assertEqual(context["direct_subnet"], "192.168.0.32/27")
        self.assertEqual(context["direct_source"], "cellular gateway LAN settings")

    def test_build_management_context_uses_management_interface_when_subnet_matches(self):
        self.service.client.get_device_management_interface.return_value = {
            "wan1": {"staticIp": "10.20.30.40", "staticSubnetMask": "255.255.255.0"}
        }

        context = self.service._build_management_context(
            {"serial": "Q2XX-1234", "productType": "mx", "lanIp": "10.20.30.40"}
        )

        self.assertEqual(context["direct_ip"], "10.20.30.40")
        self.assertEqual(context["direct_subnet"], "10.20.30.40/255.255.255.0")
        self.assertEqual(context["direct_source"], "management interface settings")
