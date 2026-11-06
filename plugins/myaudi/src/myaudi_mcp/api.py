"""Client for the vehicle API behind the myAudi app (CARIAD "BFF", EMEA region)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx2 as httpx

from .auth import AudiAuth
from .errors import AudiError, AuthenticationError, RateLimitError

BFF_BASE = "https://emea.bff.cariad.digital/vehicle/v1"

API_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "content-version": "1",
    "user-agent": "WeConnect/3 CFNetwork/1331.0.7 Darwin/21.4.0",
    "accept-language": "de-de",
    "cache-control": "no-cache",
}

# Status "jobs" the selectivestatus endpoint understands. Only the ones a vehicle has
# an active capability for are requested; asking for others makes the call fail.
KNOWN_JOBS = (
    "access",
    "activeventilation",
    "auxiliaryheating",
    "batteryChargingCare",
    "batterySupport",
    "charging",
    "chargingProfiles",
    "climatisation",
    "climatisationTimers",
    "departureTimers",
    "fuelStatus",
    "lvBattery",
    "measurements",
    "oilLevel",
    "readiness",
    "userCapabilities",
    "vehicleHealthInspection",
    "vehicleHealthWarnings",
    "vehicleLights",
)

_VIN = re.compile(r"^[A-Z0-9]{17}$")


def check_vin(vin: str) -> str:
    vin = vin.strip().upper()
    if not _VIN.match(vin):
        raise AudiError(f"'{vin}' is not a valid VIN (17 letters and digits).")
    return vin


def active_jobs(vehicle: dict[str, Any], wanted: tuple[str, ...] | list[str] = KNOWN_JOBS) -> list[str]:
    """Jobs from `wanted` that the vehicle has an enabled capability for.

    A capability with a non-empty `status` list is switched off (for example an expired licence).
    """
    enabled = {
        cap.get("id") for cap in vehicle.get("capabilities") or [] if isinstance(cap, dict) and not cap.get("status")
    }
    return [job for job in wanted if job in enabled]


class AudiApi:
    def __init__(
        self,
        auth: AudiAuth,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._auth = auth
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        # Opened on demand, so the API object outlives the MCP session that closed the last client.
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=BFF_BASE, headers=API_HEADERS, timeout=self._timeout, transport=self._transport
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        content = json.dumps(body) if body is not None else None
        response: httpx.Response | None = None
        for attempt in range(2):
            token = await self._auth.access_token(force_refresh=attempt > 0)
            headers = {"authorization": f"Bearer {token}", "weconnect-trace-id": str(uuid.uuid4()).upper()}
            try:
                response = await self._client.request(method, path, content=content, headers=headers)
            except httpx.TimeoutException as exc:
                raise AudiError("The myAudi backend did not answer in time. Try again in a moment.") from exc
            except httpx.HTTPError as exc:
                raise AudiError(f"Could not reach the myAudi backend: {exc}") from exc
            if response.status_code != 401:
                break
        assert response is not None

        status = response.status_code
        if status == 401:
            raise AuthenticationError("The myAudi backend does not accept the login. Check the account details.")
        if status == 429:
            raise RateLimitError(
                "The myAudi backend reports too many requests from this account. Wait a few minutes before the "
                "next request."
            )
        if status == 403:
            raise AudiError(
                "The myAudi backend refused this request (HTTP 403). The vehicle may not support it, the service "
                "licence may have expired, or you are not the vehicle's primary user."
            )
        if status >= 400:
            raise AudiError(f"The myAudi backend answered with HTTP {status}: {response.text[:300]}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 204 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise AudiError("The myAudi backend returned something that is not JSON.") from exc
        return data if isinstance(data, dict) else {"data": data}

    async def list_vehicles(self) -> list[dict[str, Any]]:
        data = self._json(await self._request("GET", "/vehicles"))
        return [v for v in data.get("data") or [] if isinstance(v, dict) and v.get("vin")]

    async def selective_status(self, vin: str, jobs: list[str]) -> dict[str, Any]:
        if not jobs:
            return {}
        # The endpoint expects the comma-separated list as is, so the query is not url-encoded.
        path = f"/vehicles/{check_vin(vin)}/selectivestatus?jobs={','.join(jobs)}"
        return self._json(await self._request("GET", path))

    async def parking_position(self, vin: str) -> dict[str, Any] | None:
        """Last parking position, or None while the car is driving or has not reported one."""
        response = await self._request("GET", f"/vehicles/{check_vin(vin)}/parkingposition")
        if response.status_code == 204:
            return None
        return self._json(response).get("data") or None

    async def command(self, vin: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._request("POST", f"/vehicles/{check_vin(vin)}/{path}", body if body is not None else {})
        return self._json(response)
