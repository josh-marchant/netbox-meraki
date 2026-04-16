from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from netbox_meraki.forms import PluginSettingsForm
from netbox_meraki.models import PluginSettings
from netbox_meraki.views import ConfigView


class PluginSettingsModelTests(TestCase):
    def test_encrypts_and_decrypts_api_key(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("secret-token")

        self.assertNotEqual(settings.meraki_api_key_encrypted, "secret-token")
        self.assertEqual(settings.get_meraki_api_key(), "secret-token")
        self.assertTrue(settings.has_meraki_api_key)

    def test_clear_api_key_removes_stored_value(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("secret-token")

        settings.clear_meraki_api_key()

        self.assertEqual(settings.meraki_api_key_encrypted, "")
        self.assertEqual(settings.get_meraki_api_key(), "")
        self.assertFalse(settings.has_meraki_api_key)


class PluginSettingsFormTests(TestCase):
    def test_blank_submit_preserves_existing_api_key(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("original-token")
        settings.save()

        form = PluginSettingsForm(
            data={
                "meraki_base_url": "https://api.meraki.com/api/v1",
                "mx_device_role": settings.mx_device_role,
                "ms_device_role": settings.ms_device_role,
                "mr_device_role": settings.mr_device_role,
                "mg_device_role": settings.mg_device_role,
                "mv_device_role": settings.mv_device_role,
                "mt_device_role": settings.mt_device_role,
                "default_device_role": settings.default_device_role,
                "sync_mode": settings.sync_mode,
                "device_name_transform": settings.device_name_transform,
                "site_name_transform": settings.site_name_transform,
                "vlan_name_transform": settings.vlan_name_transform,
                "ssid_name_transform": settings.ssid_name_transform,
                "site_tags": settings.site_tags,
                "device_tags": settings.device_tags,
                "vlan_tags": settings.vlan_tags,
                "prefix_tags": settings.prefix_tags,
                "api_requests_per_second": str(settings.api_requests_per_second),
            },
            instance=settings,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.get_meraki_api_key(), "original-token")

    def test_new_api_key_replaces_existing_value(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("old-token")
        settings.save()

        form = PluginSettingsForm(
            data={
                "meraki_base_url": "https://api.meraki.com/api/v1",
                "meraki_api_key": "new-token",
                "mx_device_role": settings.mx_device_role,
                "ms_device_role": settings.ms_device_role,
                "mr_device_role": settings.mr_device_role,
                "mg_device_role": settings.mg_device_role,
                "mv_device_role": settings.mv_device_role,
                "mt_device_role": settings.mt_device_role,
                "default_device_role": settings.default_device_role,
                "sync_mode": settings.sync_mode,
                "device_name_transform": settings.device_name_transform,
                "site_name_transform": settings.site_name_transform,
                "vlan_name_transform": settings.vlan_name_transform,
                "ssid_name_transform": settings.ssid_name_transform,
                "site_tags": settings.site_tags,
                "device_tags": settings.device_tags,
                "vlan_tags": settings.vlan_tags,
                "prefix_tags": settings.prefix_tags,
                "api_requests_per_second": str(settings.api_requests_per_second),
            },
            instance=settings,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.get_meraki_api_key(), "new-token")

    def test_clear_checkbox_removes_api_key(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("old-token")
        settings.save()

        form = PluginSettingsForm(
            data={
                "meraki_base_url": "https://api.meraki.com/api/v1",
                "clear_api_key": "on",
                "mx_device_role": settings.mx_device_role,
                "ms_device_role": settings.ms_device_role,
                "mr_device_role": settings.mr_device_role,
                "mg_device_role": settings.mg_device_role,
                "mv_device_role": settings.mv_device_role,
                "mt_device_role": settings.mt_device_role,
                "default_device_role": settings.default_device_role,
                "sync_mode": settings.sync_mode,
                "device_name_transform": settings.device_name_transform,
                "site_name_transform": settings.site_name_transform,
                "vlan_name_transform": settings.vlan_name_transform,
                "ssid_name_transform": settings.ssid_name_transform,
                "site_tags": settings.site_tags,
                "device_tags": settings.device_tags,
                "vlan_tags": settings.vlan_tags,
                "prefix_tags": settings.prefix_tags,
                "api_requests_per_second": str(settings.api_requests_per_second),
            },
            instance=settings,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.get_meraki_api_key(), "")


class ConfigViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request_with_messages(self, method, path, data=None):
        request = getattr(self.factory, method.lower())(path, data=data or {})
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = type("User", (), {"has_perm": lambda self, perm: True})()
        return request

    def test_config_page_never_renders_plaintext_api_key(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("visible-secret")
        settings.save()

        response = ConfigView.as_view()(self._request_with_messages("GET", "/config/"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("API key stored", content)
        self.assertNotIn("visible-secret", content)

    def test_config_post_updates_url_and_preserves_blank_api_key(self):
        settings = PluginSettings.get_settings()
        settings.set_meraki_api_key("keep-me")
        settings.save()

        with patch("netbox_meraki.views.redirect", return_value=HttpResponse(status=302)):
            response = ConfigView.as_view()(
                self._request_with_messages(
                    "POST",
                    "/config/",
                    data={
                        "meraki_base_url": "https://api.meraki.cn/api/v1",
                        "mx_device_role": settings.mx_device_role,
                        "ms_device_role": settings.ms_device_role,
                        "mr_device_role": settings.mr_device_role,
                        "mg_device_role": settings.mg_device_role,
                        "mv_device_role": settings.mv_device_role,
                        "mt_device_role": settings.mt_device_role,
                        "default_device_role": settings.default_device_role,
                        "sync_mode": settings.sync_mode,
                        "device_name_transform": settings.device_name_transform,
                        "site_name_transform": settings.site_name_transform,
                        "vlan_name_transform": settings.vlan_name_transform,
                        "ssid_name_transform": settings.ssid_name_transform,
                        "site_tags": settings.site_tags,
                        "device_tags": settings.device_tags,
                        "vlan_tags": settings.vlan_tags,
                        "prefix_tags": settings.prefix_tags,
                        "api_requests_per_second": str(settings.api_requests_per_second),
                    },
                )
            )

        self.assertEqual(response.status_code, 302)
        settings.refresh_from_db()
        self.assertEqual(settings.meraki_base_url, "https://api.meraki.cn/api/v1")
        self.assertEqual(settings.get_meraki_api_key(), "keep-me")
