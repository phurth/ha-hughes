"""Number platform for the Hughes Power Watchdog BLE integration (Gen2 only).

Number entities:
  - Backlight Level (0–5): controls display brightness (CMD_SET_BACKLIGHT)

Reference: Android HughesGen2GattCallback handleCommand() backlight
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN, GEN2_BACKLIGHT_MAX
from .coordinator import HughesCoordinator
from .sensor import _make_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hughes number entities from a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "")

    async_add_entities([HughesBacklightNumber(coordinator, address, device_name)])


class HughesBacklightNumber(CoordinatorEntity[HughesCoordinator], NumberEntity):
    """Number entity for the Hughes Gen2 display backlight level.

    Range 0–5; sends CMD_SET_BACKLIGHT (0x07).
    Current value is read back from CMD_DL_REPORT byte offset 24.
    """

    _attr_has_entity_name = True
    _attr_name = "Backlight Level"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:brightness-5"
    _attr_native_min_value = 0.0
    _attr_native_max_value = float(GEN2_BACKLIGHT_MAX)
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: HughesCoordinator, address: str, device_name: str
    ) -> None:
        super().__init__(coordinator)
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_backlight"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected and we have state."""
        return self.coordinator.connected and self.coordinator.state is not None

    @property
    def native_value(self) -> float | None:
        """Return the current backlight level."""
        if self.coordinator.state is None:
            return None
        bl = self.coordinator.state.line1.backlight
        return float(bl) if bl is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the backlight level."""
        level = int(value)
        _LOGGER.info("Setting backlight to %d", level)
        await self.coordinator.async_set_backlight(level)
