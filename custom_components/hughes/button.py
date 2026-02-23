"""Button platform for the Hughes Power Watchdog BLE integration (Gen2 only).

Buttons:
  - Reset Energy: clears cumulative kWh counter (CMD_ENERGY_RESET)
  - Sync Time: sends current UTC time to device (CMD_SET_TIME)

Reference: Android HughesGen2GattCallback handleCommand() energy reset and time sync
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import HughesCoordinator
from .sensor import _make_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hughes button entities from a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "")

    async_add_entities([
        HughesResetEnergyButton(coordinator, address, device_name),
        HughesSyncTimeButton(coordinator, address, device_name),
    ])


class HughesResetEnergyButton(CoordinatorEntity[HughesCoordinator], ButtonEntity):
    """Button to reset the cumulative energy (kWh) counter.

    Sends CMD_ENERGY_RESET (0x03) with no body.
    The energy field in subsequent DLReports will begin counting from zero.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Reset Energy"
    _attr_icon = "mdi:counter"

    def __init__(
        self, coordinator: HughesCoordinator, address: str, device_name: str
    ) -> None:
        super().__init__(coordinator)
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_reset_energy"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected."""
        return self.coordinator.connected

    async def async_press(self) -> None:
        """Send energy reset command."""
        _LOGGER.info("Reset energy button pressed")
        await self.coordinator.async_reset_energy()


class HughesSyncTimeButton(CoordinatorEntity[HughesCoordinator], ButtonEntity):
    """Button to synchronize the device clock to current UTC time.

    Sends CMD_SET_TIME (0x06) with 6-byte body: Year-2000, Month, Day, Hour, Min, Sec.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Sync Time"
    _attr_icon = "mdi:clock-sync"

    def __init__(
        self, coordinator: HughesCoordinator, address: str, device_name: str
    ) -> None:
        super().__init__(coordinator)
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_sync_time"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected."""
        return self.coordinator.connected

    async def async_press(self) -> None:
        """Sync current time to device."""
        _LOGGER.info("Sync time button pressed")
        await self.coordinator.async_sync_time()
