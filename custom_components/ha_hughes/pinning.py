"""ha-ble-pinning, tolerating an older copy loaded earlier in the same run.

Home Assistant installs one shared copy of ha-ble-pinning for all custom
integrations. When this integration is updated and needs a newer version,
another integration may already have imported the old one during this start;
Python keeps that copy until Home Assistant restarts. Then the adapter choice
(0.2.0) is missing. Rather than failing to load, connect with Home
Assistant's normal routing and say that a restart enables the choice.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

_LOGGER = logging.getLogger(__name__)

try:
    from ha_ble_pinning import (
        AUTOMATIC,
        connection_path,
        establish_connection_via,
    )
    from ha_ble_pinning import adapter_options as _adapter_options

    PINNING_READY = True

    def adapter_options(
        scanner_devices: Iterable[Any], current: str | None = None, scanners: Iterable[Any] = ()
    ) -> dict[str, str]:
        """Picker choices; 0.2.0 (loaded earlier in this run) lacks ``scanners``."""
        try:
            return _adapter_options(scanner_devices, current, scanners=scanners)
        except TypeError:
            return _adapter_options(scanner_devices, current)
except ImportError:  # An older ha-ble-pinning is loaded; a restart fixes it.
    PINNING_READY = False
    AUTOMATIC = "automatic"
    _warned = False

    try:
        from ha_ble_pinning import connection_path  # present since 0.1.0
    except ImportError:

        def connection_path(client: Any) -> Any:  # type: ignore[misc]
            scanner = getattr(client, "_connected_scanner", None)
            return type("Path", (), {"source": getattr(scanner, "source", None)})()

    def adapter_options(
        scanner_devices: Iterable[Any], current: str | None = None, scanners: Iterable[Any] = ()
    ) -> dict[str, str]:
        """Only Automatic (plus the stored choice) until Home Assistant restarts."""
        options = {AUTOMATIC: "Automatic (restart Home Assistant to choose an adapter)"}
        if current and current != AUTOMATIC:
            options[current] = f"{current} (used after Home Assistant restarts)"
        return options

    async def establish_connection_via(
        device: Any,
        name: str,
        *,
        adapter: str | None,
        allow_fallback: bool = True,
        scanner_devices: Iterable[Any] = (),
        disconnected_callback: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Connect with Home Assistant's routing; the stored choice waits for a restart."""
        global _warned  # noqa: PLW0603
        from bleak import BleakClient  # noqa: PLC0415
        from bleak_retry_connector import establish_connection  # noqa: PLC0415

        if adapter and adapter != AUTOMATIC and not _warned:
            _warned = True
            _LOGGER.warning(
                "An older ha-ble-pinning was loaded earlier in this start, so the chosen "
                "Bluetooth adapter (%s) is not used yet. Restart Home Assistant once to use it",
                adapter,
            )
        return await establish_connection(
            BleakClient, device, name, disconnected_callback=disconnected_callback, **kwargs
        )
