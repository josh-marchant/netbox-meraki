from unittest.mock import patch

import requests
from django.test import TestCase

from netbox_meraki.meraki_client import MerakiAPIClient
from netbox_meraki.models import PluginSettings


class MockResponse:
    def __init__(self, payload, headers=None, status_code=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


class MerakiAPIClientTests(TestCase):
    def setUp(self):
        settings = PluginSettings.get_settings()
        settings.meraki_base_url = "https://api.meraki.com/api/v1"
        settings.set_meraki_api_key("token")
        settings.save()

    def test_rejects_non_https_base_url(self):
        with self.assertRaises(ValueError):
            MerakiAPIClient(base_url="http://api.meraki.com/api/v1")

    def test_rejects_non_meraki_host(self):
        with self.assertRaises(ValueError):
            MerakiAPIClient(base_url="https://example.com/api/v1")

    @patch("netbox_meraki.meraki_client.time.sleep")
    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_paginates_list_responses(self, request_mock, _sleep_mock):
        request_mock.side_effect = [
            MockResponse([{"id": 1}], headers={"Link": '<https://api.meraki.com/api/v1/organizations?startingAfter=abc>; rel="next"'}),
            MockResponse([{"id": 2}], headers={}),
        ]
        client = MerakiAPIClient()
        payload = client.get_organizations()
        self.assertEqual(payload, [{"id": 1}, {"id": 2}])
        self.assertEqual(request_mock.call_args_list[0].kwargs["params"], {"perPage": 1000})
        self.assertIsNone(request_mock.call_args_list[1].kwargs["params"])

    @patch("netbox_meraki.meraki_client.time.sleep")
    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_paginates_relative_links(self, request_mock, _sleep_mock):
        request_mock.side_effect = [
            MockResponse([{"id": 1}], headers={"Link": '</api/v1/organizations?startingAfter=abc>; rel="next"'}),
            MockResponse([{"id": 2}], headers={}),
        ]
        client = MerakiAPIClient()
        payload = client.get_organizations()
        self.assertEqual(payload, [{"id": 1}, {"id": 2}])
        self.assertEqual(
            request_mock.call_args_list[1].args[1],
            "https://api.meraki.com/api/v1/organizations?startingAfter=abc",
        )

    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_switch_ports_request_has_no_pagination_params(self, request_mock):
        request_mock.return_value = MockResponse([{"portId": "1"}], headers={})
        client = MerakiAPIClient()
        payload = client.get_switch_ports("Q2XX-1234")
        self.assertEqual(payload, [{"portId": "1"}])
        self.assertIsNone(request_mock.call_args.kwargs["params"])

    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_appliance_vlans_request_has_no_pagination_params(self, request_mock):
        request_mock.return_value = MockResponse([{"id": "10"}], headers={})
        client = MerakiAPIClient()
        payload = client.get_appliance_vlans("N_123")
        self.assertEqual(payload, [{"id": "10"}])
        self.assertIsNone(request_mock.call_args.kwargs["params"])

    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_device_request_has_no_pagination_params(self, request_mock):
        request_mock.return_value = MockResponse({"serial": "Q2XX-1234"}, headers={})
        client = MerakiAPIClient()
        payload = client.get_device("Q2XX-1234")
        self.assertEqual(payload, {"serial": "Q2XX-1234"})
        self.assertIsNone(request_mock.call_args.kwargs["params"])

    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_wireless_ssid_detail_request_has_no_pagination_params(self, request_mock):
        request_mock.return_value = MockResponse({"number": 3, "defaultVlanId": 20}, headers={})
        client = MerakiAPIClient()
        payload = client.get_wireless_ssid("N_123", 3)
        self.assertEqual(payload, {"number": 3, "defaultVlanId": 20})
        self.assertIsNone(request_mock.call_args.kwargs["params"])

    @patch("netbox_meraki.meraki_client.time.sleep")
    @patch("netbox_meraki.meraki_client.requests.Session.request")
    def test_retries_after_rate_limit(self, request_mock, sleep_mock):
        request_mock.side_effect = [
            MockResponse([], headers={"Retry-After": "7"}, status_code=429),
            MockResponse([{"id": 1}], headers={}),
        ]
        client = MerakiAPIClient()
        payload = client.get_organizations()
        self.assertEqual(payload, [{"id": 1}])
        sleep_mock.assert_any_call(7)

    def test_loads_url_and_api_key_from_plugin_settings(self):
        client = MerakiAPIClient()

        self.assertEqual(client.base_url, "https://api.meraki.com/api/v1")
        self.assertEqual(client.api_key, "token")

    def test_missing_key_raises_friendly_error(self):
        settings = PluginSettings.get_settings()
        settings.clear_meraki_api_key()
        settings.save()

        with self.assertRaisesMessage(ValueError, "Meraki API key is required"):
            MerakiAPIClient()
