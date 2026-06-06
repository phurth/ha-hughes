"""Hughes Power Watchdog BLE integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_GENERATION, DOMAIN, GEN2
from .coordinator import HughesCoordinator

_LOGGER = logging.getLogger(__name__)

# Platforms supported by all generations
_PLATFORMS_COMMON: list[str] = [
    "binary_sensor",
    "sensor",
]

# Platforms only meaningful for Gen2 (relay, backlight, commands)
_PLATFORMS_GEN2: list[str] = [
    "button",
    "number",
    "switch",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hughes Power Watchdog from a config entry."""
    coordinator = HughesCoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Load platforms; Gen2-specific platforms only if this is a Gen2 device
    generation = entry.data.get(CONF_GENERATION, "gen1")
    platforms = _PLATFORMS_COMMON + (
        _PLATFORMS_GEN2 if generation == GEN2 else []
    )

    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Connect in the background; entities show unavailable until first data arrives.
    # A MAC-derived startup delay staggers simultaneous BLE integration startups
    # after an HA restart, reducing adapter contention when multiple BLE integrations
    # (EasyTouch x3, OneControl, Hughes) all attempt to connect at the same time.
    # The last MAC octet gives a deterministic 0–12.75s spread — same device always
    # gets the same delay, no user configuration needed.
    async def _bg_connect() -> None:
        address = entry.data[CONF_ADDRESS]
        mac_offset = int(address.replace(":", ""), 16) & 0xFF
        startup_delay = mac_offset / 20.0  # 0–12.75s based on last MAC octet
        _LOGGER.debug(
            "Hughes %s: startup delay %.1fs (MAC-derived)",
            address,
            startup_delay,
        )
        import asyncio
        await asyncio.sleep(startup_delay)
        try:
            await coordinator.async_connect()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to connect to Hughes Power Watchdog")

    entry.async_create_background_task(hass, _bg_connect(), "hughes_initial_connect")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    generation = entry.data.get(CONF_GENERATION, "gen1")
    platforms = _PLATFORMS_COMMON + (
        _PLATFORMS_GEN2 if generation == GEN2 else []
    )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unload_ok:
        coordinator: HughesCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()

    return unload_ok
