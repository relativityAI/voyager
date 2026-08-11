"""HTTP client for the Voyager API (Render-deployed, subject to cold starts).

Handles the operational realities:
- Render free tier sleeps; first request back may be 502/503/connection-reset
  while it boots. Data requests get generous read timeouts and cold-start retries.
- Health probes use short timeouts and distinguish ok / waking / down / auth.
- 503s that carry a JSON ``detail`` (real FastAPI errors, e.g. pull limits) are
  never retried; only boot-time 503s with a non-JSON body are.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

CONNECT_TIMEOUT = 10
DATA_TIMEOUT = 120
HEALTH_TIMEOUT = 10
JOB_TIMEOUT = 30
WAKE_RETRIES = 20
WAKE_GAP_SECONDS = 5
RETRYABLE_STATUS = {502, 503}


@dataclass
class Response:
    status_code: int
    json: Any
    text: str
    elapsed_ms: float
    headers: dict


class PanelHTTPError(Exception):
    def __init__(self, status_code: int, detail: str, response: Optional[Response] = None):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.response = response


def _has_json_detail(text: str) -> bool:
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(body, dict) and "detail" in body


class VoyagerClient:
    def __init__(self, base_url: str, api_key: str = "", admin_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.admin_key = admin_key or ""
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _headers(self, admin: bool) -> dict:
        if admin:
            return {"X-Voyager-Admin-Key": self.admin_key} if self.admin_key else {}
        return {"X-API-Key": self.api_key} if self.api_key else {}

    # -- low-level ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        admin: bool = False,
        timeout: int = DATA_TIMEOUT,
        ok_status: tuple = (200, 201, 202),
        retries: int = 3,
    ) -> Response:
        if not self.base_url:
            raise PanelHTTPError(0, "No API endpoint configured.")
        headers = self._headers(admin)
        attempt = 0
        while True:
            attempt += 1
            start = time.perf_counter()
            try:
                resp = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT, timeout),
                )
                elapsed = (time.perf_counter() - start) * 1000
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                response = Response(
                    status_code=resp.status_code,
                    json=data,
                    text=resp.text,
                    elapsed_ms=round(elapsed, 1),
                    headers=dict(resp.headers),
                )
                if resp.status_code in ok_status:
                    return response
                if resp.status_code in RETRYABLE_STATUS and not _has_json_detail(
                    resp.text
                ):
                    if attempt <= retries:
                        time.sleep(WAKE_GAP_SECONDS)
                        continue
                raise PanelHTTPError(resp.status_code, self._detail(resp), response)
            except (requests.ConnectionError, requests.Timeout):
                if attempt <= retries:
                    time.sleep(WAKE_GAP_SECONDS)
                    continue
                raise PanelHTTPError(0, "API unreachable (is the Render instance asleep?)") from None

    @staticmethod
    def _detail(resp: requests.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("detail"):
                return str(body["detail"])
        except ValueError:
            pass
        return (resp.text or "").strip()[:500] or f"HTTP {resp.status_code}"

    # -- convenience ---------------------------------------------------------

    def get(self, path: str, params=None, admin=False, timeout=DATA_TIMEOUT, retries=3,
            ok_status=(200, 201, 202)) -> Response:
        return self.request("GET", path, params=params, admin=admin, timeout=timeout,
                            retries=retries, ok_status=ok_status)

    def post(self, path: str, params=None, json=None, admin=False, timeout=DATA_TIMEOUT,
             retries=3, ok_status=(200, 201, 202)) -> Response:
        return self.request(
            "POST", path, params=params, json=json, admin=admin, timeout=timeout,
            retries=retries, ok_status=ok_status,
        )

    def delete(self, path: str, admin=False, retries=3) -> Response:
        return self.request("DELETE", path, admin=admin, ok_status=(200,), retries=retries)

    # -- health / wake --------------------------------------------------------

    def health(self) -> dict:
        """Probe /healthz. Returns state: ok | waking | down | no_config | auth."""
        if not self.base_url:
            return {"state": "no_config", "ok": False}
        try:
            resp = self.request("GET", "/healthz", timeout=HEALTH_TIMEOUT, retries=0)
            if resp.status_code == 200:
                return {"state": "ok", "ok": True, "elapsed_ms": resp.elapsed_ms}
            return {"state": "waking", "ok": False, "elapsed_ms": resp.elapsed_ms}
        except PanelHTTPError as exc:
            if exc.status_code == 401 or exc.status_code == 403:
                return {"state": "auth", "ok": False, "detail": exc.detail}
            return {"state": "waking", "ok": False, "detail": exc.detail}
        except Exception as exc:  # noqa: BLE001
            return {"state": "waking", "ok": False, "detail": str(exc)}

    def wake(self, progress=None) -> dict:
        """Wait for the API to come up (Render cold start)."""
        waited = 0.0
        for i in range(WAKE_RETRIES):
            status = self.health()
            if status["ok"]:
                return {"ok": True, "waited_seconds": round(waited, 1), "state": status["state"]}
            if status["state"] in ("auth", "no_config"):
                return {"ok": False, "waited_seconds": round(waited, 1), **status}
            if progress:
                progress(f"Waking API… attempt {i + 1}/{WAKE_RETRIES} ({waited:.0f}s)")
            time.sleep(WAKE_GAP_SECONDS)
            waited += WAKE_GAP_SECONDS
        return {
            "ok": False,
            "waited_seconds": round(waited, 1),
            "state": "waking",
            "detail": "API did not come up within the wait window.",
        }

    def version(self) -> str:
        try:
            resp = self.get("/openapi.json", timeout=HEALTH_TIMEOUT)
            if resp.json and isinstance(resp.json, dict):
                return str(resp.json.get("info", {}).get("version", ""))
        except PanelHTTPError:
            pass
        return ""
