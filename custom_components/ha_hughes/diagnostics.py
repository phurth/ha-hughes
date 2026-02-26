"""Diagnostics support for the Hughes Power Watchdog BLE integration.

Provides a "Download diagnostics" dump with all coordinator and protocol
state for troubleshooting BLE connectivity and data parsing issues.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import HughesCoordinator

# No secrets to redact for Hughes (no auth credentials)
TO_REDACT_CONFIG: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Connection / health state
    connection: dict[str, Any] = {
        "connected": coordinator.connected,
        "data_healthy": coordinator.data_healthy,
        "last_data_age_seconds": (
            round(coordinator.last_data_age, 1)
            if coordinator.last_data_age is not None
            else None
        ),
        "reconnect_failures": coordinator._reconnect_failures,
        "generation": coordinator.generation,
        "is_enhanced": coordinator.is_enhanced,
        "is_dual_line": coordinator.is_dual_line,
        "device_name": coordinator.device_name,
    }

    def _line_dict(line: Any) -> dict[str, Any]:
        return {
            "voltage_v": line.voltage,
            "current_a": line.current,
            "power_w": line.power,
            "energy_kwh": line.energy,
            "frequency_hz": line.frequency,
            "error_code": line.error_code,
            "error_text": line.error_text,
            "relay_on": line.relay_on,
            "neutral_detection": line.neutral_detection,
            "backlight": line.backlight,
            "output_voltage_v": line.output_voltage,
            "boost": line.boost,
            "temperature_f": line.temperature_f,
        }

    # Parsed state
    state_data: dict[str, Any] = {}
    if coordinator.state:
        s = coordinator.state
        state_data = {
            "generation": s.generation,
            "is_enhanced": s.is_enhanced,
            "is_dual_line": s.is_dual_line,
            "line1": _line_dict(s.line1),
            "line2": _line_dict(s.line2) if s.line2 is not None else None,
            "last_seen_age_seconds": (
                round(coordinator.last_data_age, 1)
                if coordinator.last_data_age is not None
                else None
            ),
        }

    # Raw bytes for protocol debugging (Gen2 only)
    raw_hex: str | None = None
    if coordinator.state and coordinator.state.raw_bytes is not None:
        raw_hex = coordinator.state.raw_bytes.hex(" ")

    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT_CONFIG),
        "connection": connection,
        "state": state_data,
        "last_raw_dl_report_hex": raw_hex,
    }
