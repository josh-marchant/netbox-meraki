"""Meraki API client for the hardened NetBox Meraki plugin."""

import logging
import time
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger("netbox_meraki")


class MerakiAPIClient:
    DEFAULT_TIMEOUT = 30
    DEFAULT_PER_PAGE = 1000
    ALLOWED_HOST_SUFFIXES = (".meraki.com", ".meraki.cn")

    def __init__(self, api_key=None, base_url=None):
        if api_key is None or base_url is None:
            plugin_settings = __import__("netbox_meraki.models", fromlist=["PluginSettings"]).PluginSettings.get_settings()
        else:
            plugin_settings = None

        self.api_key = api_key if api_key is not None else plugin_settings.get_meraki_api_key()
        self.base_url = self.validate_base_url(
            base_url if base_url is not None else plugin_settings.meraki_base_url
        )
        self.timeout = self.DEFAULT_TIMEOUT

        if not self.api_key:
            raise ValueError("Meraki API key is required")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Cisco-Meraki-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

        self.last_request_time = 0.0
        self.min_request_interval = self._build_rate_limit_interval()

    def _build_rate_limit_interval(self):
        try:
            plugin_settings = __import__("netbox_meraki.models", fromlist=["PluginSettings"]).PluginSettings.get_settings()
            if not plugin_settings.enable_api_throttling:
                return 0.0
            return 1.0 / max(1, plugin_settings.api_requests_per_second)
        except Exception:
            return 0.2

    @classmethod
    def validate_base_url(cls, base_url):
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise ValueError("Meraki API base URL must use HTTPS")
        if host != "api.meraki.com" and not host.endswith(cls.ALLOWED_HOST_SUFFIXES):
            raise ValueError("Meraki API base URL must point to a Meraki-hosted API domain")
        return base_url.rstrip("/")

    def _rate_limit(self):
        if self.min_request_interval <= 0:
            return
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()

    def _extract_next_link(self, response):
        link_header = response.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' not in part:
                continue
            if "<" in part and ">" in part:
                return part[part.index("<") + 1 : part.index(">")]
        return None

    def _request(self, method, url, params=None):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._rate_limit()
                response = self.session.request(method, url, params=params or None, timeout=self.timeout)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    logger.warning("Meraki API rate limited; retrying in %s seconds", retry_after)
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                data = response.json() if response.content else {}
                return response, data
            except requests.exceptions.RequestException:
                if attempt >= max_retries - 1:
                    raise
                time.sleep(2)
        raise RuntimeError("Meraki request retry loop exhausted")

    def _request_json(self, method, endpoint, params=None):
        url = endpoint if endpoint.startswith("http") else urljoin(f"{self.base_url}/", endpoint.lstrip("/"))
        return self._request(method, url, dict(params or {}))[1]

    def _request_paginated_list(self, method, endpoint, params=None):
        url = endpoint if endpoint.startswith("http") else urljoin(f"{self.base_url}/", endpoint.lstrip("/"))
        request_params = dict(params or {})
        request_params.setdefault("perPage", self.DEFAULT_PER_PAGE)
        aggregated = []

        while url:
            response, data = self._request(method, url, request_params)
            if not isinstance(data, list):
                return data
            aggregated.extend(data)
            next_link = self._extract_next_link(response)
            if not next_link:
                return aggregated
            url = urljoin(f"{self.base_url}/", next_link)
            request_params = None

        return aggregated

    def get_organizations(self):
        return self._request_paginated_list("GET", "organizations")

    def get_networks(self, organization_id):
        return self._request_paginated_list("GET", f"organizations/{organization_id}/networks")

    def get_inventory_devices(self, organization_id):
        return self._request_paginated_list("GET", f"organizations/{organization_id}/inventory/devices")

    def get_device_availabilities(self, organization_id):
        return self._request_paginated_list("GET", f"organizations/{organization_id}/devices/availabilities")

    def get_device(self, serial):
        return self._request_json("GET", f"devices/{serial}")

    def get_device_cellular_gateway_lan(self, serial):
        return self._request_json("GET", f"devices/{serial}/cellularGateway/lan")

    def get_device_management_interface(self, serial):
        return self._request_json("GET", f"devices/{serial}/managementInterface")

    def get_wireless_ssids(self, network_id):
        try:
            return self._request_json("GET", f"networks/{network_id}/wireless/ssids")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 404):
                return []
            raise

    def get_wireless_ssid(self, network_id, number):
        return self._request_json("GET", f"networks/{network_id}/wireless/ssids/{number}")

    def get_switch_ports(self, serial):
        try:
            return self._request_json("GET", f"devices/{serial}/switch/ports")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            raise

    def get_appliance_vlans(self, network_id):
        try:
            return self._request_json("GET", f"networks/{network_id}/appliance/vlans")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 404):
                return []
            raise
