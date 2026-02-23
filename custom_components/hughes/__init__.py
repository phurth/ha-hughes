"""Hughes Power Watchdog BLE integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
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

    # Connect in the background; entities show unavailable until first data arrives
    async def _bg_connect() -> None:
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
