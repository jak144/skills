"""An in-process stand-in for the VW identity login, the CARIAD token endpoint and the vehicle API."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl

import httpx2 as httpx

from myaudi_mcp.auth import CLIENT_ID

USERNAME = "driver@example.com"
PASSWORD = "correct horse"
EV_VIN = "WAUZZZF40NA012345"
ICE_VIN = "WAUZZZ8W0PA054321"

STAMP = "2026-11-06T07:50:00Z"

EV_STATUS: dict[str, Any] = {
    "access": {
        "accessStatus": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "overallStatus": "safe",
                "doorLockStatus": "locked",
                "doors": [
                    {"name": "bonnet", "status": ["closed"]},
                    {"name": "frontLeft", "status": ["locked", "closed"]},
                    {"name": "trunk", "status": ["locked", "closed"]},
                ],
                "windows": [
                    {"name": "frontLeft", "status": ["closed"]},
                    {"name": "sunRoof", "status": ["unsupported"]},
                ],
            }
        }
    },
    "charging": {
        "batteryStatus": {
            "value": {"carCapturedTimestamp": STAMP, "currentSOC_pct": 78, "cruisingRangeElectric_km": 301}
        },
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "remainingChargingTimeToComplete_min": 45,
                "chargingState": "charging",
                "chargeMode": "manual",
                "chargePower_kW": 11.0,
                "chargeRate_kmph": 52,
                "chargeType": "ac",
            }
        },
        "chargingSettings": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "maxChargeCurrentAC": "maximum",
                "autoUnlockPlugWhenChargedAC": "off",
                "targetSOC_pct": 80,
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "plugConnectionState": "connected",
                "plugLockState": "locked",
                "externalPower": "active",
                "ledColor": "green",
            }
        },
    },
    "climatisation": {
        "climatisationSettings": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "targetTemperature_C": 22.0,
                "targetTemperature_F": 72.0,
                "unitInCar": "celsius",
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "windowHeatingEnabled": False,
                "zoneFrontLeftEnabled": True,
                "zoneFrontRightEnabled": True,
            }
        },
        "climatisationStatus": {
            "value": {"carCapturedTimestamp": STAMP, "remainingClimatisationTime_min": 0, "climatisationState": "off"}
        },
        "windowHeatingStatus": {
            "value": {
                "carCapturedTimestamp": STAMP,
                "windowHeatingStatus": [
                    {"windowLocation": "front", "windowHeatingState": "off"},
                    {"windowLocation": "rear", "windowHeatingState": "invalid"},
                ],
            }
        },
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": "2026-11-06T07:55:00Z",
                "carType": "electric",
                "primaryEngine": {"type": "electric", "currentSOC_pct": 78, "remainingRange_km": 301},
                "totalRange_km": 301,
            }
        }
    },
    "measurements": {
        "odometerStatus": {"value": {"carCapturedTimestamp": STAMP, "odometer": 23456}},
        "temperatureOutsideStatus": {"value": {"carCapturedTimestamp": STAMP, "temperatureOutside_K": 281.15}},
        "temperatureBatteryStatus": {
            "error": {"message": "Upstream service responded with an unexpected status", "code": 4111, "retry": True}
        },
    },
    "readiness": {
        "readinessStatus": {"value": {"connectionState": {"isOnline": False, "isActive": False}}},
    },
}

ICE_STATUS: dict[str, Any] = {
    "access": {
        "accessStatus": {
            "value": {
                "carCapturedTimestamp": "2026-11-05T18:00:00Z",
                "overallStatus": "unsafe",
                "doorLockStatus": "unlocked",
                "doors": [{"name": "frontRight", "status": ["unlocked", "open"]}],
                "windows": [{"name": "rearLeft", "status": ["open"]}],
            }
        }
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": "2026-11-05T18:00:00Z",
                "carType": "gasoline",
                "primaryEngine": {"type": "gasoline", "currentFuelLevel_pct": 34, "remainingRange_km": 290},
                "totalRange_km": 290,
            }
        }
    },
    "measurements": {
        "fuelLevelStatus": {
            "value": {"carCapturedTimestamp": "2026-11-05T18:00:00Z", "currentFuelLevel_pct": 34, "carType": "gasoline"}
        },
    },
    "vehicleHealthInspection": {
        "maintenanceStatus": {
            "value": {
                "carCapturedTimestamp": "2026-11-05T18:00:00Z",
                "inspectionDue_days": 120,
                "inspectionDue_km": 9000,
                "oilServiceDue_days": 60,
                "oilServiceDue_km": 4000,
                "mileage_km": 61234,
            }
        }
    },
}


def capabilities(*ids: str, disabled: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    caps = [{"id": i, "status": [], "userDisablingAllowed": False} for i in ids]
    return caps + [{"id": i, "status": [1004], "userDisablingAllowed": False} for i in disabled]


VEHICLES = [
    {
        "vin": EV_VIN,
        "nickname": "Q4",
        "model": "Q4 Sportback e-tron",
        "capabilities": capabilities(
            "access",
            "charging",
            "climatisation",
            "fuelStatus",
            "measurements",
            "readiness",
            "parkingPosition",
            disabled=("vehicleHealthInspection",),
        ),
    },
]


class FakeAudi:
    """Callable for httpx.MockTransport. Records what the client sent."""

    def __init__(self, vehicles: list[dict[str, Any]] | None = None, statuses: dict[str, dict[str, Any]] | None = None):
        self.vehicles = copy.deepcopy(vehicles if vehicles is not None else VEHICLES)
        self.statuses = copy.deepcopy(statuses if statuses is not None else {EV_VIN: EV_STATUS, ICE_VIN: ICE_STATUS})
        self.position: dict[str, Any] | None = {"lat": 48.7665, "lon": 11.4258, "carCapturedTimestamp": STAMP}
        self.password = PASSWORD
        self.refresh_works = True
        self.logins = 0
        self.refreshes = 0
        self.token_number = 0
        self.access_token = ""
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.status_queries: list[str] = []
        self.api_headers: list[httpx.Headers] = []
        self._challenge = ""
        self._state = ""

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "identity.vwgroup.io":
            return self._identity(request)
        if request.url.path == "/auth/v1/idk/oidc/token":
            return self._token(request)
        if request.url.path.startswith("/vehicle/v1/"):
            return self._vehicle_api(request)
        return httpx.Response(404)

    # --- identity.vwgroup.io -------------------------------------------------------------
    def _identity(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        signin = f"/signin-service/v1/{CLIENT_ID}"
        if path == "/oidc/v1/authorize":
            params = dict(request.url.params)
            assert params["client_id"] == CLIENT_ID
            assert params["code_challenge_method"] == "S256"
            self._challenge, self._state = params["code_challenge"], params["state"]
            return redirect(f"/signin-service/v1/signin/{CLIENT_ID}?relayState=relay-1")
        if path == f"/signin-service/v1/signin/{CLIENT_ID}":
            return httpx.Response(200, text=EMAIL_PAGE.format(action=f"{signin}/login/identifier"))
        if path == f"{signin}/login/identifier":
            form = form_data(request)
            assert form == {"_csrf": "csrf-1", "relayState": "relay-1", "hmac": "hmac-1", "email": USERNAME}
            return httpx.Response(200, text=PASSWORD_PAGE)
        if path == f"{signin}/login/authenticate":
            form = form_data(request)
            assert form["_csrf"] == "csrf-2" and form["hmac"] == "hmac-2" and form["email"] == USERNAME
            if form["password"] != self.password:
                return redirect(f"{signin}/login/authenticate?relayState=relay-1&error=login.errors.password_invalid")
            return redirect(f"/oidc/v1/oauth/sso?clientId={CLIENT_ID}&relayState=relay-1&userId=user-1&HMAC=x")
        if path == "/oidc/v1/oauth/sso":
            return redirect(f"/signin-service/v1/consent/users/user-1/{CLIENT_ID}?relayState=relay-1")
        if path.startswith("/signin-service/v1/consent/users/"):
            return redirect("https://identity.vwgroup.io/oidc/v1/oauth/client/callback/success?user_id=user-1")
        if path == "/oidc/v1/oauth/client/callback/success":
            return redirect(f"myaudi:///?code=auth-code-1&state={self._state}")
        return httpx.Response(404)

    # --- token endpoint ------------------------------------------------------------------
    def _token(self, request: httpx.Request) -> httpx.Response:
        form = form_data(request)
        assert form["client_id"] == CLIENT_ID
        if form["grant_type"] == "authorization_code":
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == self._challenge
            assert form["code"] == "auth-code-1" and form["redirect_uri"] == "myaudi:///"
            self.logins += 1
        elif form["grant_type"] == "refresh_token":
            if not self.refresh_works:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refreshes += 1
        else:
            return httpx.Response(400)
        self.token_number += 1
        self.access_token = f"access-{self.token_number}"
        return httpx.Response(
            200,
            json={
                "access_token": self.access_token,
                "refresh_token": f"refresh-{self.token_number}",
                "id_token": "id",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    # --- vehicle API ---------------------------------------------------------------------
    def _vehicle_api(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.access_token}":
            return httpx.Response(401)
        self.api_headers.append(request.headers)
        parts = request.url.path.removeprefix("/vehicle/v1/").split("/")
        if parts == ["vehicles"]:
            return httpx.Response(200, json={"data": self.vehicles})
        vin, endpoint = parts[1], "/".join(parts[2:])
        if endpoint == "selectivestatus":
            query = request.url.query.decode()
            self.status_queries.append(query)
            jobs = query.removeprefix("jobs=").split(",")
            status = self.statuses.get(vin, {})
            return httpx.Response(207, json={job: status[job] for job in jobs if job in status})
        if endpoint == "parkingposition":
            return httpx.Response(200, json={"data": self.position}) if self.position else httpx.Response(204)
        if request.method == "POST":
            self.commands.append((endpoint, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"data": {"requestID": f"req-{len(self.commands)}"}})
        return httpx.Response(404)


def redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": location})


def form_data(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.content.decode()))


EMAIL_PAGE = """<!DOCTYPE html><html><body>
<form id="emailPasswordForm" method="POST" action="{action}">
  <input type="hidden" name="_csrf" value="csrf-1"/>
  <input type="hidden" name="relayState" value="relay-1"/>
  <input type="hidden" name="hmac" value="hmac-1"/>
  <input type="email" name="email" value=""/>
  <button type="submit">Weiter</button>
</form></body></html>"""

PASSWORD_PAGE = """<!DOCTYPE html><html><body><div id="app"></div>
<script>
  window._IDK = {
    templateModel: {"clientLegalEntityModel":{},"hmac":"hmac-2","relayState":"relay-1","postAction":"login/authenticate","identifier":"driver@example.com","error":null},
    csrf_parameterName: '_csrf',
    csrf_token: 'csrf-2',
    currentLocale: 'de'
  };
</script></body></html>"""
