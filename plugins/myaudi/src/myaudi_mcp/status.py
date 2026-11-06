"""Turn the raw selectivestatus answer into a short, readable vehicle summary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FUEL_TYPES = {"gasoline", "petrol", "diesel", "fuel", "cng", "lpg"}

# Jobs the summary reads. The raw-status tool can still ask for any other job.
SUMMARY_JOBS = (
    "access",
    "charging",
    "climatisation",
    "fuelStatus",
    "measurements",
    "readiness",
    "vehicleHealthInspection",
)


def section(data: dict[str, Any], job: str, name: str) -> dict[str, Any] | None:
    """data[job][name]["value"], or None when it is missing or the backend reported an error for it."""
    node = (data.get(job) or {}).get(name) if isinstance(data.get(job), dict) else None
    if not isinstance(node, dict) or "error" in node:
        return None
    value = node.get("value", node)
    return value if isinstance(value, dict) else None


def job_errors(data: dict[str, Any]) -> dict[str, str]:
    """Parts of a (multi-status) answer that the backend could not deliver, with its reason."""
    errors: dict[str, str] = {}
    for job, parts in data.items():
        if not isinstance(parts, dict):
            continue
        for name, node in parts.items():
            if isinstance(node, dict) and isinstance(node.get("error"), dict):
                errors[f"{job}.{name}"] = str(node["error"].get("message") or node["error"].get("info") or "error")
    return errors


def _kelvin_to_celsius(value: Any) -> float | None:
    return round(float(value) - 273.15, 1) if isinstance(value, (int, float)) else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest_timestamp(data: Any) -> datetime | None:
    latest: datetime | None = None
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stamp = _parse_time(node.get("carCapturedTimestamp"))
            if stamp and (latest is None or stamp > latest):
                latest = stamp
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return latest


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _compact(value: Any) -> Any:
    """Drop None values and empty containers so the summary only shows what the car reported."""
    if isinstance(value, dict):
        cleaned = {k: _compact(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v is not None and v != {} and v != []}
    if isinstance(value, list):
        return [_compact(v) for v in value if v is not None]
    return value


def _engines(range_status: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for key in ("primaryEngine", "secondaryEngine") if isinstance(e := range_status.get(key), dict)]


def summarize(
    data: dict[str, Any], vehicle: dict[str, Any] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    vehicle = vehicle or {}
    now = now or datetime.now(timezone.utc)

    fuel_range = section(data, "fuelStatus", "rangeStatus") or {}
    fuel_level = section(data, "measurements", "fuelLevelStatus") or {}
    measured_range = section(data, "measurements", "rangeStatus") or {}
    battery = section(data, "charging", "batteryStatus") or {}
    engines = _engines(fuel_range)
    electric = next((e for e in engines if e.get("type") == "electric"), {})
    combustion = next((e for e in engines if e.get("type") in FUEL_TYPES), {})
    car_type = _first(fuel_range.get("carType"), fuel_level.get("carType"))

    summary: dict[str, Any] = {
        "vin": vehicle.get("vin"),
        "name": vehicle.get("nickname"),
        "model": vehicle.get("model"),
        "car_type": car_type,
        "battery": {
            "soc_pct": _first(
                electric.get("currentSOC_pct"), fuel_level.get("currentSOC_pct"), battery.get("currentSOC_pct")
            ),
            "range_km": _first(
                electric.get("remainingRange_km"),
                measured_range.get("electricRange"),
                battery.get("cruisingRangeElectric_km"),
            ),
        },
        "fuel": {
            "type": combustion.get("type"),
            "level_pct": _first(combustion.get("currentFuelLevel_pct"), fuel_level.get("currentFuelLevel_pct")),
            "range_km": _first(
                combustion.get("remainingRange_km"),
                measured_range.get("gasolineRange"),
                measured_range.get("dieselRange"),
            ),
        },
        "total_range_km": _first(fuel_range.get("totalRange_km"), measured_range.get("totalRange_km")),
        "adblue_range_km": _first(fuel_range.get("adBlueRange"), measured_range.get("adBlueRange")),
    }

    charging = section(data, "charging", "chargingStatus") or {}
    charge_settings = section(data, "charging", "chargingSettings") or {}
    plug = section(data, "charging", "plugStatus") or {}
    summary["charging"] = {
        "state": charging.get("chargingState"),
        "power_kw": charging.get("chargePower_kW"),
        "rate_km_per_h": charging.get("chargeRate_kmph"),
        "remaining_min": charging.get("remainingChargingTimeToComplete_min"),
        "type": charging.get("chargeType"),
        "mode": charging.get("chargeMode"),
        "target_soc_pct": charge_settings.get("targetSOC_pct"),
        "max_current_ac": _first(
            charge_settings.get("maxChargeCurrentAC_A"), charge_settings.get("maxChargeCurrentAC")
        ),
        "plug": plug.get("plugConnectionState"),
        "plug_lock": plug.get("plugLockState"),
        "external_power": plug.get("externalPower"),
    }

    climate = section(data, "climatisation", "climatisationStatus") or {}
    climate_settings = section(data, "climatisation", "climatisationSettings") or {}
    window_heating = section(data, "climatisation", "windowHeatingStatus") or {}
    summary["climatisation"] = {
        "state": climate.get("climatisationState"),
        "remaining_min": climate.get("remainingClimatisationTime_min"),
        "target_temperature_c": climate_settings.get("targetTemperature_C"),
        "without_external_power": climate_settings.get("climatisationWithoutExternalPower"),
        "window_heating_enabled": climate_settings.get("windowHeatingEnabled"),
        "window_heating": {
            w["windowLocation"]: w.get("windowHeatingState")
            for w in window_heating.get("windowHeatingStatus") or []
            if isinstance(w, dict) and w.get("windowLocation") and w.get("windowHeatingState") not in (None, "invalid")
        },
    }

    access = section(data, "access", "accessStatus") or {}
    doors = [d for d in access.get("doors") or [] if isinstance(d, dict) and d.get("name")]
    windows = [w for w in access.get("windows") or [] if isinstance(w, dict) and w.get("name")]
    summary["access"] = (
        {
            "overall": access.get("overallStatus"),
            "doors_locked": {"locked": True, "unlocked": False}.get(access.get("doorLockStatus") or ""),
            "open_doors": [d["name"] for d in doors if "open" in (d.get("status") or [])],
            "unlocked_doors": [d["name"] for d in doors if "unlocked" in (d.get("status") or [])],
            "open_windows": [w["name"] for w in windows if "open" in (w.get("status") or [])],
        }
        if access
        else None
    )

    odometer = section(data, "measurements", "odometerStatus") or {}
    outside = section(data, "measurements", "temperatureOutsideStatus") or {}
    hv_temp = section(data, "measurements", "temperatureBatteryStatus") or {}
    summary["odometer_km"] = odometer.get("odometer")
    summary["outside_temperature_c"] = _kelvin_to_celsius(outside.get("temperatureOutside_K"))
    summary["hv_battery_temperature_c"] = {
        "min": _kelvin_to_celsius(hv_temp.get("temperatureHvBatteryMin_K")),
        "max": _kelvin_to_celsius(hv_temp.get("temperatureHvBatteryMax_K")),
    }

    maintenance = section(data, "vehicleHealthInspection", "maintenanceStatus") or {}
    summary["service"] = {
        "inspection_due_days": maintenance.get("inspectionDue_days"),
        "inspection_due_km": maintenance.get("inspectionDue_km"),
        "oil_service_due_days": maintenance.get("oilServiceDue_days"),
        "oil_service_due_km": maintenance.get("oilServiceDue_km"),
    }
    if summary["odometer_km"] is None:
        summary["odometer_km"] = maintenance.get("mileage_km")

    readiness = section(data, "readiness", "readinessStatus") or {}
    connection = readiness.get("connectionState") if isinstance(readiness.get("connectionState"), dict) else {}
    summary["online"] = connection.get("isOnline")

    latest = _latest_timestamp(data)
    if latest:
        summary["last_update"] = latest.isoformat().replace("+00:00", "Z")
        summary["data_age_min"] = max(0, round((now - latest).total_seconds() / 60))

    errors = job_errors(data)
    if errors:
        summary["unavailable"] = errors
    return _compact(summary)
