"""The MCP tools end to end: an in-process MCP client talks to the server, which talks to FakeAudi."""

import json

import httpx2 as httpx
import pytest
from mcp import Client

from fake_audi import EV_VIN, ICE_VIN, PASSWORD, USERNAME, FakeAudi, capabilities
from myaudi_mcp import server
from myaudi_mcp.api import AudiApi
from myaudi_mcp.auth import AudiAuth


@pytest.fixture
def fake(monkeypatch):
    fake = FakeAudi()
    monkeypatch.setattr(server, "service", server.Service())
    transport = fake.transport()
    server.service._api = AudiApi(AudiAuth(USERNAME, PASSWORD, transport=transport), transport=transport)
    monkeypatch.delenv("MYAUDI_VEHICLE", raising=False)
    return fake


async def call(name, **arguments):
    async with Client(server.mcp) as client:
        result = await client.call_tool(name, arguments)
    text = result.content[0].text
    return result.is_error, (text if result.is_error else json.loads(text))


async def test_tools_are_listed_with_safety_annotations():
    async with Client(server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == {
        "audi_list_vehicles",
        "audi_get_status",
        "audi_get_raw_status",
        "audi_get_position",
        "audi_start_climatisation",
        "audi_stop_climatisation",
        "audi_start_charging",
        "audi_stop_charging",
        "audi_wake_vehicle",
    }
    assert tools["audi_get_status"].annotations.read_only_hint is True
    assert tools["audi_start_climatisation"].annotations.read_only_hint is False
    temperature = tools["audi_start_climatisation"].input_schema["properties"]["target_temperature_c"]
    assert "°C" in temperature["description"]


async def test_status_asks_only_for_active_jobs(fake):
    error, status = await call("audi_get_status")

    assert not error
    assert status["battery"] == {"soc_pct": 78, "range_km": 301}
    assert fake.status_queries == ["jobs=access,charging,climatisation,fuelStatus,measurements,readiness"]
    headers = fake.api_headers[-1]
    assert headers["content-version"] == "1" and headers["weconnect-trace-id"]


async def test_start_climatisation_keeps_car_settings_and_overrides_temperature(fake):
    error, result = await call("audi_start_climatisation", target_temperature_c=21.3, window_heating=True)

    assert not error
    assert result["accepted"] is True and result["request_id"] == "req-1"
    endpoint, body = fake.commands[0]
    assert endpoint == "climatisation/start"
    assert body == {
        "targetTemperature": 21.5,
        "targetTemperatureUnit": "celsius",
        "climatisationWithoutExternalPower": True,
        "windowHeatingEnabled": True,
        "climatizationAtUnlock": False,
        "zoneFrontLeftEnabled": True,
        "zoneFrontRightEnabled": True,
    }


async def test_temperature_outside_the_cars_range_is_rejected_before_sending(fake):
    error, message = await call("audi_start_climatisation", target_temperature_c=35)

    assert error and "target_temperature_c" in message
    assert fake.commands == []


async def test_stop_climatisation_and_charging(fake):
    assert not (await call("audi_stop_climatisation"))[0]
    assert not (await call("audi_start_charging"))[0]
    assert not (await call("audi_stop_charging"))[0]

    assert [c[0] for c in fake.commands] == ["climatisation/stop", "charging/start", "charging/stop"]


async def test_command_without_the_capability_is_not_sent(fake):
    fake.vehicles[0]["capabilities"] = capabilities("access", "fuelStatus", disabled=("climatisation",))

    error, message = await call("audi_start_climatisation")

    assert error and "no active 'climatisation' service" in message
    assert fake.commands == []


async def test_several_cars_need_a_choice(fake):
    fake.vehicles.append(
        {"vin": ICE_VIN, "nickname": "A4", "model": "A4 Avant", "capabilities": capabilities("access", "fuelStatus")}
    )

    error, message = await call("audi_get_status")
    assert error and "several vehicles" in message and ICE_VIN in message

    error, status = await call("audi_get_status", vehicle="a4")
    assert not error and status["fuel"]["level_pct"] == 34

    error, status = await call("audi_get_status", vehicle=EV_VIN.lower())
    assert not error and status["name"] == "Q4"


async def test_position_with_map_link_and_while_driving(fake):
    error, position = await call("audi_get_position")
    assert not error and position["latitude"] == 48.7665
    assert position["map_url"].endswith("query=48.7665,11.4258")

    fake.position = None
    error, position = await call("audi_get_position")
    assert not error and position["position"] is None


async def test_expired_token_on_the_api_is_refreshed_once(fake):
    await call("audi_list_vehicles")
    fake.access_token = "rotated-on-the-server"

    error, result = await call("audi_list_vehicles")

    assert not error and result["vehicles"][0]["vin"] == EV_VIN
    assert fake.refreshes == 1


async def test_rate_limit_is_explained(fake):
    original = fake._vehicle_api

    def vehicle_api(request):
        if "selectivestatus" in request.url.path:
            return httpx.Response(429)
        return original(request)

    fake._vehicle_api = vehicle_api

    error, message = await call("audi_get_status")
    assert error and "too many requests" in message


async def test_missing_credentials_are_a_readable_tool_error(monkeypatch):
    monkeypatch.setattr(server, "service", server.Service())
    monkeypatch.delenv("MYAUDI_USERNAME", raising=False)
    monkeypatch.delenv("MYAUDI_PASSWORD", raising=False)

    error, message = await call("audi_get_status")
    assert error and "MYAUDI_USERNAME and MYAUDI_PASSWORD are not set" in message
