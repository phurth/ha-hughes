"""Switch platform for the Hughes Power Watchdog BLE integration (Gen2 only).

Switches:
  - Relay: main power relay on/off (CMD_SET_OPEN)
  - Neutral Detection: enable/disable neutral detection monitoring (CMD_NEUTRAL_DETECTION)

Reference: Android HughesGen2GattCallback handleCommand() relay and neutral detection
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
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
    """Set up Hughes switch entities from a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "")

    async_add_entities([
        HughesRelaySwitch(coordinator, address, device_name),
        HughesNeutralDetectionSwitch(coordinator, address, device_name),
    ])


class HughesRelaySwitch(CoordinatorEntity[HughesCoordinator], SwitchEntity):
    """Switch entity for the Hughes Gen2 main power relay.

    Controls CMD_SET_OPEN (0x0B): 0x01 = relay ON, 0x02 = relay OFF.
    State is read back from CMD_DL_REPORT byte offset 33.
    """

    _attr_has_entity_name = True
    _attr_name = "Relay"
    _attr_icon = "mdi:power-socket"

    def __init__(
        self, coordinator: HughesCoordinator, address: str, device_name: str
    ) -> None:
        super().__init__(coordinator)
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_relay"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected and we have at least one DLReport."""
        return self.coordinator.connected and self.coordinator.state is not None

    @property
    def is_on(self) -> bool | None:
        """Return True if relay is ON."""
        if self.coordinator.state is None:
            return None
        return self.coordinator.state.line1.relay_on

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn relay on."""
        _LOGGER.info("Relay turn ON")
        await self.coordinator.async_set_relay(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn relay off."""
        _LOGGER.info("Relay turn OFF")
        await self.coordinator.async_set_relay(False)


class HughesNeutralDetectionSwitch(CoordinatorEntity[HughesCoordinator], SwitchEntity):
    """Switch entity for Hughes Gen2 neutral detection monitoring.

    Controls CMD_NEUTRAL_DETECTION (0x0D): 0x01 = enable, 0x00 = disable.
    State is read back from CMD_DL_REPORT byte offset 25.
    """

    _attr_has_entity_name = True
    _attr_name = "Neutral Detection"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:electric-switch"

    def __init__(
        self, coordinator: HughesCoordinator, address: str, device_name: str
    ) -> None:
        super().__init__(coordinator)
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_neutral_detection"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected and we have state."""
        return self.coordinator.connected and self.coordinator.state is not None

    @property
    def is_on(self) -> bool | None:
        """Return True if neutral detection is enabled."""
        if self.coordinator.state is None:
            return None
        return self.coordinator.state.line1.neutral_detection

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable neutral detection."""
        _LOGGER.info("Neutral detection: enable")
        await self.coordinator.async_set_neutral_detection(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable neutral detection."""
        _LOGGER.info("Neutral detection: disable")
        await self.coordinator.async_set_neutral_detection(False)
