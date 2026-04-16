from types import SimpleNamespace

from django.test import SimpleTestCase

from netbox_meraki.forms import MerakiVLANResolutionRuleForm, PluginSettingsForm, ReviewItemEditForm, ScheduledSyncForm


class ReviewItemEditFormTests(SimpleTestCase):
    def test_populates_initial_data_from_review_item(self):
        item = SimpleNamespace(
            get_final_data=lambda: {"name": "Branch AP", "serial": "Q2XX-1234"},
            notes="check naming",
        )
        form = ReviewItemEditForm(review_item=item)
        self.assertIn('"name": "Branch AP"', form.initial["editable_data"])
        self.assertEqual(form.initial["notes"], "check naming")

    def test_rejects_invalid_json(self):
        form = ReviewItemEditForm(data={"editable_data": "{bad json", "notes": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("editable_data", form.errors)


class ScheduledSyncFormTests(SimpleTestCase):
    def test_custom_interval_requires_value(self):
        form = ScheduledSyncForm(
            data={
                "name": "Nightly sync",
                "interval": "custom",
                "sync_mode": "review",
                "organization_id": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("custom_interval", form.errors)

    def test_accepts_standard_interval_without_custom_value(self):
        form = ScheduledSyncForm(
            data={
                "name": "Nightly sync",
                "interval": "1440",
                "sync_mode": "review",
                "organization_id": "",
            }
        )
        self.assertTrue(form.is_valid())

    def test_one_time_schedule_requires_scheduled_time(self):
        form = ScheduledSyncForm(
            data={
                "name": "One-off sync",
                "interval": "0",
                "sync_mode": "review",
                "organization_id": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("scheduled_time", form.errors)


class MerakiVLANResolutionRuleFormTests(SimpleTestCase):
    def test_populates_organization_choices(self):
        form = MerakiVLANResolutionRuleForm(
            organizations=[{"id": "1", "name": "Org One"}],
            networks=[],
        )
        self.assertEqual(form.fields["meraki_organization_id"].choices[0], ("", "Any Organization"))
        self.assertIn(("1", "Org One"), form.fields["meraki_organization_id"].choices)

    def test_populates_network_choices(self):
        form = MerakiVLANResolutionRuleForm(
            organizations=[],
            networks=[{"id": "N_1", "name": "Branch Network"}],
        )
        self.assertEqual(form.fields["meraki_network_id"].choices[0], ("", "Any Network"))
        self.assertIn(("N_1", "Branch Network"), form.fields["meraki_network_id"].choices)

    def test_preserves_unknown_saved_org_and_network_ids(self):
        instance = SimpleNamespace(meraki_organization_id="999", meraki_network_id="N_999")
        form = MerakiVLANResolutionRuleForm(instance=instance, organizations=[], networks=[])
        self.assertIn(("999", "Unknown organization (999)"), form.fields["meraki_organization_id"].choices)
        self.assertIn(("N_999", "Unknown network (N_999)"), form.fields["meraki_network_id"].choices)

    def test_rejects_new_network_selection_without_organization(self):
        form = MerakiVLANResolutionRuleForm(
            data={
                "name": "Rule",
                "meraki_organization_id": "",
                "meraki_network_id": "N_123",
                "site": "",
                "vlan_group": "",
                "priority": "100",
                "enabled": "on",
                "description": "",
            },
            organizations=[],
            networks=[],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("meraki_network_id", form.errors)


class PluginSettingsFormValidationTests(SimpleTestCase):
    def test_rejects_non_meraki_base_url(self):
        form = PluginSettingsForm(
            data={
                "meraki_base_url": "https://example.com/api/v1",
                "mx_device_role": "MX",
                "ms_device_role": "MS",
                "mr_device_role": "MR",
                "mg_device_role": "MG",
                "mv_device_role": "MV",
                "mt_device_role": "MT",
                "default_device_role": "Default",
                "sync_mode": "review",
                "device_name_transform": "keep",
                "site_name_transform": "keep",
                "vlan_name_transform": "keep",
                "ssid_name_transform": "keep",
                "site_tags": "",
                "device_tags": "",
                "vlan_tags": "",
                "prefix_tags": "",
                "api_requests_per_second": "5",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("meraki_base_url", form.errors)
