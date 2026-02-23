"""Sensor platform for the Hughes Power Watchdog BLE integration.

Entities created for all devices (Gen1 and Gen2):
  - L1 Voltage, Current, Power, Energy, Frequency (always available)
  - L1 Error, L1 Error Code (diagnostic)
  - L2 Voltage, Current, Power, Energy, Frequency (available only on dual-line devices)
  - L2 Error, L2 Error Code (diagnostic; available only on dual-line devices)

Additional entities for Gen2 enhanced models (E8/V8/E9/V9):
  - L1 Output Voltage
  - L1 Temperature (°F)

Reference: Android HughesWatchdogDevicePlugin.kt / HughesGen2GattCallback MQTT payloads
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import HughesCoordinator
from .models import HughesLineData, HughesState


@dataclass(frozen=True, kw_only=True)
class HughesSensorDescription(SensorEntityDescription):
    """Describe a Hughes sensor entity."""

    value_fn: Callable[[HughesLineData], float | int | str | None]
    is_l2: bool = False          # True if this entity reads from line2
    gen2_enhanced_only: bool = False  # True if only valid on Gen2 enhanced models


# ---------------------------------------------------------------------------
# Sensor definitions
# ---------------------------------------------------------------------------

_L1_SENSORS: tuple[HughesSensorDescription, ...] = (
    HughesSensorDescription(
        key="voltage_l1",
        name="L1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
        value_fn=lambda d: d.voltage,
    ),
    HughesSensorDescription(
        key="current_l1",
        name="L1 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
        value_fn=lambda d: d.current,
    ),
    HughesSensorDescription(
        key="power_l1",
        name="L1 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=1,
        value_fn=lambda d: d.power,
    ),
    HughesSensorDescription(
        key="energy_l1",
        name="L1 Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:meter-electric",
        suggested_display_precision=3,
        value_fn=lambda d: d.energy,
    ),
    HughesSensorDescription(
        key="frequency_l1",
        name="L1 Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
        value_fn=lambda d: d.frequency,
    ),
    HughesSensorDescription(
        key="error_l1",
        name="L1 Error",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
        value_fn=lambda d: d.error_text,
    ),
    HughesSensorDescription(
        key="error_code_l1",
        name="L1 Error Code",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:numeric",
        value_fn=lambda d: d.error_code,
    ),
    # Gen2 enhanced only
    HughesSensorDescription(
        key="output_voltage_l1",
        name="L1 Output Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt-outline",
        suggested_display_precision=2,
        gen2_enhanced_only=True,
        value_fn=lambda d: d.output_voltage,
    ),
    HughesSensorDescription(
        key="temperature_l1",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        gen2_enhanced_only=True,
        value_fn=lambda d: d.temperature_f,
    ),
)

_L2_SENSORS: tuple[HughesSensorDescription, ...] = (
    HughesSensorDescription(
        key="voltage_l2",
        name="L2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
        is_l2=True,
        value_fn=lambda d: d.voltage,
    ),
    HughesSensorDescription(
        key="current_l2",
        name="L2 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
        is_l2=True,
        value_fn=lambda d: d.current,
    ),
    HughesSensorDescription(
        key="power_l2",
        name="L2 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=1,
        is_l2=True,
        value_fn=lambda d: d.power,
    ),
    HughesSensorDescription(
        key="energy_l2",
        name="L2 Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:meter-electric",
        suggested_display_precision=3,
        is_l2=True,
        value_fn=lambda d: d.energy,
    ),
    HughesSensorDescription(
        key="frequency_l2",
        name="L2 Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
        is_l2=True,
        value_fn=lambda d: d.frequency,
    ),
    HughesSensorDescription(
        key="error_l2",
        name="L2 Error",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
        is_l2=True,
        value_fn=lambda d: d.error_text,
    ),
    HughesSensorDescription(
        key="error_code_l2",
        name="L2 Error Code",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:numeric",
        is_l2=True,
        value_fn=lambda d: d.error_code,
    ),
)

SENSOR_DESCRIPTIONS: tuple[HughesSensorDescription, ...] = _L1_SENSORS + _L2_SENSORS


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

def _make_device_info(address: str, device_name: str) -> DeviceInfo:
    display_name = (
        f"Power Watchdog {device_name}" if device_name else f"Power Watchdog {address}"
    )
    return DeviceInfo(
        identifiers={(DOMAIN, address)},
        name=display_name,
        manufacturer="Hughes",
        model=device_name or "Power Watchdog",
        connections={("bluetooth", address)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hughes sensor entities from a config entry."""
    coordinator: HughesCoordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "")

    async_add_entities(
        HughesSensor(coordinator, address, device_name, desc)
        for desc in SENSOR_DESCRIPTIONS
        if not desc.gen2_enhanced_only or coordinator.is_enhanced
    )


# ---------------------------------------------------------------------------
# Entity class
# ---------------------------------------------------------------------------

class HughesSensor(CoordinatorEntity[HughesCoordinator], SensorEntity):
    """A Hughes Power Watchdog sensor entity."""

    entity_description: HughesSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HughesCoordinator,
        address: str,
        device_name: str,
        description: HughesSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._address = address
        mac = address.replace(":", "").lower()
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = _make_device_info(address, device_name)

    def _get_line_data(self, state: HughesState) -> HughesLineData | None:
        """Return the appropriate line data for this entity."""
        if self.entity_description.is_l2:
            return state.line2
        return state.line1

    @property
    def available(self) -> bool:
        """Available when connected and the required data is present."""
        if not self.coordinator.connected or self.coordinator.state is None:
            return False
        state = self.coordinator.state
        if self.entity_description.gen2_enhanced_only and not state.is_enhanced:
            return False
        if self.entity_description.is_l2 and not state.is_dual_line:
            return False
        return True

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        if self.coordinator.state is None:
            return None
        line = self._get_line_data(self.coordinator.state)
        if line is None:
            return None
        return self.entity_description.value_fn(line)
