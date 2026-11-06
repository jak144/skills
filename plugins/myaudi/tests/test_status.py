from datetime import datetime, timezone

from fake_audi import EV_STATUS, EV_VIN, ICE_STATUS, VEHICLES
from myaudi_mcp.api import active_jobs
from myaudi_mcp.status import summarize

NOW = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)


def test_electric_car_summary():
    summary = summarize(EV_STATUS, VEHICLES[0], now=NOW)

    assert summary["vin"] == EV_VIN
    assert summary["car_type"] == "electric"
    assert summary["battery"] == {"soc_pct": 78, "range_km": 301}
    assert "fuel" not in summary
    assert summary["charging"] == {
        "state": "charging",
        "power_kw": 11.0,
        "rate_km_per_h": 52,
        "remaining_min": 45,
        "type": "ac",
        "mode": "manual",
        "target_soc_pct": 80,
        "max_current_ac": "maximum",
        "plug": "connected",
        "plug_lock": "locked",
        "external_power": "active",
    }
    assert summary["climatisation"] == {
        "state": "off",
        "remaining_min": 0,
        "target_temperature_c": 22.0,
        "without_external_power": True,
        "window_heating_enabled": False,
        "window_heating": {"front": "off"},
    }
    assert summary["access"] == {"overall": "safe", "doors_locked": True}
    assert summary["odometer_km"] == 23456
    assert summary["outside_temperature_c"] == 8.0
    assert summary["online"] is False
    assert summary["last_update"] == "2026-11-06T07:55:00Z"
    assert summary["data_age_min"] == 5
    assert summary["unavailable"] == {
        "measurements.temperatureBatteryStatus": "Upstream service responded with an unexpected status"
    }


def test_combustion_car_summary():
    summary = summarize(ICE_STATUS, {"vin": "WAUZZZ8W0PA054321", "nickname": "A4"}, now=NOW)

    assert summary["fuel"] == {"type": "gasoline", "level_pct": 34, "range_km": 290}
    assert "battery" not in summary and "charging" not in summary
    assert summary["access"] == {
        "overall": "unsafe",
        "doors_locked": False,
        "open_doors": ["frontRight"],
        "unlocked_doors": ["frontRight"],
        "open_windows": ["rearLeft"],
    }
    assert summary["service"] == {
        "inspection_due_days": 120,
        "inspection_due_km": 9000,
        "oil_service_due_days": 60,
        "oil_service_due_km": 4000,
    }
    assert summary["odometer_km"] == 61234
    assert summary["data_age_min"] == 840


def test_only_enabled_capabilities_become_jobs():
    jobs = active_jobs(VEHICLES[0], ["access", "charging", "vehicleHealthInspection", "auxiliaryheating"])
    assert jobs == ["access", "charging"]
