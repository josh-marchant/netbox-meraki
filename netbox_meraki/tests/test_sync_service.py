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
