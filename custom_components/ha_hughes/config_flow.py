"""Config flow for the Hughes Power Watchdog BLE integration.

Supports automatic discovery via BLE advertisements matching:
  - Service UUID 0000FFE0 (Gen1)
  - Service UUID 000000FF (Gen2)
  - Device name prefixes: PMD*, PWS*, WD_*

Also supports manual entry by MAC address (no device name required).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_scanner_devices_by_address,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    ALL_SERVICE_UUIDS,
    CONF_ADAPTER,
    CONF_ADAPTER_FALLBACK,
    CONF_DEVICE_NAME,
    CONF_GENERATION,
    DOMAIN,
    GEN1,
    GEN1_NAME_PREFIXES,
    GEN2,
    GEN2_NAME_PREFIX,
)
from .pinning import AUTOMATIC, adapter_options

_LOGGER = logging.getLogger(__name__)


def _detect_generation(name: str) -> str:
    """Determine generation from device name. Defaults to GEN1 for unknown names."""
    if name.upper().startswith(GEN2_NAME_PREFIX.upper()):
        return GEN2
    return GEN1


def _is_hughes_device(info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this BLE advertisement looks like a Hughes Power Watchdog."""
    name = (info.name or "").upper()
    if any(name.startswith(p.upper()) for p in GEN1_NAME_PREFIXES):
        return True
    if name.startswith(GEN2_NAME_PREFIX.upper()):
        return True
    uuids_lower = [u.lower() for u in info.service_uuids]
    return any(u.lower() in uuids_lower for u in ALL_SERVICE_UUIDS)


class HughesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hughes Power Watchdog."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (Bluetooth adapter choice)."""
        return HughesOptionsFlow()

    def __init__(self) -> None:
        """Initialize."""
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    # ------------------------------------------------------------------
    # Bluetooth auto-discovery path
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        _LOGGER.debug(
            "Hughes BLE discovery: %s (%s)",
            discovery_info.name,
            discovery_info.address,
        )

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": name}

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Bluetooth discovery."""
        assert self._discovery_info is not None

        device_name = self._discovery_info.name or ""
        generation = _detect_generation(device_name)

        if user_input is not None:
            return self.async_create_entry(
                title=device_name or self._discovery_info.address,
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_GENERATION: generation,
                    CONF_DEVICE_NAME: device_name,
                },
            )

        name = device_name or self._discovery_info.address
        self.context["title_placeholders"] = {"name": name}

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": name,
                "generation": "Gen2" if generation == GEN2 else "Gen1",
            },
        )

    # ------------------------------------------------------------------
    # Manual user flow
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user-initiated config flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            # Check if this address is in recently discovered devices
            device_name = ""
            for info in async_discovered_service_info(self.hass):
                if info.address.upper() == address.upper():
                    device_name = info.name or ""
                    break

            generation = _detect_generation(device_name)

            return self.async_create_entry(
                title=device_name or f"Hughes Power Watchdog {address}",
                data={
                    CONF_ADDRESS: address,
                    CONF_GENERATION: generation,
                    CONF_DEVICE_NAME: device_name,
                },
            )

        # Populate discovered devices list for user selection
        self._discovered_devices = {}
        for info in async_discovered_service_info(self.hass):
            if info.address in self._discovered_devices:
                continue
            if _is_hughes_device(info):
                self._discovered_devices[info.address] = info

        if self._discovered_devices:
            addresses = {
                addr: f"{info.name or 'Hughes Power Watchdog'} ({addr})"
                for addr, info in self._discovered_devices.items()
            }
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required(CONF_ADDRESS): vol.In(addresses)}
                ),
                errors=errors,
            )

        # No discovered devices — fall back to manual MAC entry
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )


class HughesOptionsFlow(OptionsFlow):
    """Choose which Bluetooth adapter or proxy the Watchdog connects through.

    The Watchdog streams telemetry over a connection that stays open, so it
    occupies a slot on whatever adapter it uses. Home Assistant picks that
    adapter by signal strength and its own slot count, which can pile several
    held connections onto one adapter that can't carry them. Choosing here
    pins the Watchdog to one adapter or proxy (ha-ble-pinning).
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry
        current = entry.options.get(CONF_ADAPTER, AUTOMATIC)
        heard = async_scanner_devices_by_address(
            self.hass, entry.data[CONF_ADDRESS], connectable=True
        )
        # A connected Watchdog may stop advertising, so also list every scanner
        # Home Assistant knows, not only the ones hearing it right now.
        current_scanners = getattr(bluetooth, "async_current_scanners", None)
        choices = adapter_options(
            heard, current, scanners=current_scanners(self.hass) if current_scanners else ()
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_ADAPTER, default=current): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=value, label=label)
                            for value, label in choices.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_ADAPTER_FALLBACK,
                    default=entry.options.get(CONF_ADAPTER_FALLBACK, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
