"""Binary sensor platform for the Hughes Power Watchdog BLE integration.

Diagnostic binary sensors (all generations):
  - Connected: BLE link is active
  - Data Healthy: receiving non-stale data

Gen2 enhanced only:
  - Boost Active L1: voltage boost in progress
  - Boost Active L2: (dual-line enhanced only)

Reference: Android HughesWatchdogDevicePlugin.kt / HughesGen2GattCallback diag publishing
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import HughesCoordinator
from .models import HughesState
from .sensor import _make_device_info


@dataclass(frozen=True, kw_only=True)
class HughesBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Hughes binary sensor entity."""

    value_fn: Callable[[HughesCoordinator], bool | None]
    gen2_enhanced_only: bool = False
    is_l2: bool = False


BINARY_SENSOR_DESCRIPTIONS: tuple[HughesBinarySensorDescription, ...] = (
    HughesBinarySensorDescription(
        key="connected",
        name="Connected",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda c: c.connected,
    ),
    HughesBinarySensorDescription(
        key="data_healthy",
        name="Data Healthy",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:heart-pulse",
        # PROBLEM class: ON = problem, so invert
        value_fn=lambda c: not c.data_healthy,
    ),
    # Gen2 enhanced: boost active
    HughesBinarySensorDescription(
        key="boost_l1",
        name="L1 Boost Active",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:flash-triangle",
        gen2_enhanced_only=True,
        value_fn=lambda c: (
            c.state.line1.boost if c.state is not None else None
        ),
    ),
    HughesBinarySensorDescription(
        key="boost_l2",
        name="L2 Boost Active",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:flash-triangle",
        gen2_enhanced_only=True,
        is_l2=True,
        value_fn=lambda c: (
            c.state.line2.boost if c.state is not None and c.state.line2 is not None else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hughes binary sensor entities from a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "")

    async_add_entities(
        HughesBinarySensor(coordinator, address, device_name, desc)
        for desc in BINARY_SENSOR_DESCRIPTIONS
        if not desc.gen2_enhanced_only or coordinator.is_enhanced
    )


class HughesBinarySensor(CoordinatorEntity[HughesCoordinator], BinarySensorEntity):
    """A Hughes Power Watchdog binary sensor entity."""

    entity_description: HughesBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HughesCoordinator,
        address: str,
        device_name: str,
        description: HughesBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = _make_device_info(address, device_name)

    @property
    def available(self) -> bool:
        """Available when connected (and optionally when enhanced/dual-line confirmed)."""
        if not self.coordinator.connected:
            return False
        if self.entity_description.gen2_enhanced_only:
            state = self.coordinator.state
            if state is None or not state.is_enhanced:
                return False
            if self.entity_description.is_l2 and not state.is_dual_line:
                return False
        return True

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        return self.entity_description.value_fn(self.coordinator)
