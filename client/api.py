"""Thin HTTP client for the Voyager API."""

from typing import Any, Optional

import requests

from client.config import Config

DEFAULT_TIMEOUT = 90


class VoyagerError(Exception):
    """Raised when the Voyager API returns a non-success status."""

    def __init__(self, status_code: int, detail: str, job_id: str | None = None):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.job_id = job_id


class VoyagerClient:
    def __init__(self, config: Config):
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.admin_key = config.admin_key
        self.session = requests.Session()

    def _headers(self, admin: bool) -> dict:
        headers = {"Accept": "application/json"}
        if admin:
            headers["X-Voyager-Admin-Key"] = self.admin_key
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        admin: bool = False,
        ok_status: tuple = (200, 201, 202),
    ) -> Any:
        if not self.base_url:
            raise VoyagerError(0, "No base URL configured. Set VOYAGER_BASE_URL.")
        if admin and not self.admin_key:
            raise VoyagerError(0, "No admin key configured. Set VOYAGER_ADMIN_KEY.")
        if not admin and not self.api_key:
            raise VoyagerError(0, "No API key configured. Set VOYAGER_API_KEY.")

        resp = self.session.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json,
            headers=self._headers(admin),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code not in ok_status:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise VoyagerError(resp.status_code, str(detail))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get(self, path: str, params: Optional[dict] = None, admin: bool = False) -> Any:
        return self.request("GET", path, params=params, admin=admin)

    def post(
        self,
        path: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        admin: bool = False,
    ) -> Any:
        return self.request("POST", path, params=params, json=json, admin=admin)

    def delete(self, path: str, admin: bool = False) -> Any:
        return self.request("DELETE", path, admin=admin, ok_status=(200,))
