"""BLE coordinator for Hughes Power Watchdog devices.

Lifecycle:
  Gen1:
    1. Connect via BLE GATT
    2. Discover services, validate 0xFFE0 service present
    3. Enable notifications on 0xFFE2
    4. Receive 20-byte chunks; Gen1FrameAssembler combines pairs into 40-byte frames
    5. Parse frames → HughesState; fire HA coordinator update

  Gen2:
    1. Connect via BLE GATT
    2. Discover services, validate 0x00FF service present
    3. Enable notifications on 0xFF01
    4. Write ASCII "!%!%,protocol,open," to enter binary mode
    5. Receive "ok" acknowledgment (non-fatal if absent)
    6. Receive binary framed packets; Gen2PacketFramer reassembles
    7. Handle CMD_DL_REPORT → HughesState; fire HA coordinator update

Reference: Android HughesWatchdogDevicePlugin.kt, HughesGen2DevicePlugin.kt
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak import BleakClient, BleakError, BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_DEVICE_NAME,
    CONF_GENERATION,
    DOMAIN,
    GEN1,
    GEN1_NOTIFY_CHAR_UUID,
    GEN1_SERVICE_UUID,
    GEN2,
    GEN2_CMD_DL_REPORT,
    GEN2_ENHANCED_MODELS,
    GEN2_INIT_SETTLE_DELAY,
    GEN2_INIT_TIMEOUT,
    GEN2_NAME_PREFIX,
    GEN2_PROTOCOL_OPEN_CMD,
    GEN2_RW_CHAR_UUID,
    GEN2_SERVICE_UUID,
    OPERATION_DELAY,
    RECONNECT_BACKOFF_BASE,
    RECONNECT_BACKOFF_CAP,
    SERVICE_DISCOVERY_DELAY,
    STALE_TIMEOUT,
    WATCHDOG_INTERVAL,
)
from .models import HughesLineData, HughesState
from .protocol.gen1 import Gen1FrameAssembler
from .protocol.gen2 import Gen2CommandBuilder, Gen2PacketFramer, parse_dl_report

_LOGGER = logging.getLogger(__name__)


def _detect_enhanced(device_name: str) -> bool:
    """Return True if the Gen2 device name indicates an enhanced model (E8/V8/E9/V9)."""
    parts = device_name.upper().split("_")
    return len(parts) >= 2 and parts[1] in GEN2_ENHANCED_MODELS


class HughesCoordinator(DataUpdateCoordinator[HughesState | None]):
    """Manage BLE connection and data updates for a Hughes Power Watchdog device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Hughes {entry.data[CONF_ADDRESS]}",
        )
        self._address: str = entry.data[CONF_ADDRESS]
        self._generation: str = entry.data.get(CONF_GENERATION, GEN1)
        self._device_name: str = entry.data.get(CONF_DEVICE_NAME, "")
        self._entry = entry

        # Generation-specific flags (Gen2 only)
        self._is_enhanced: bool = (
            _detect_enhanced(self._device_name) if self._generation == GEN2 else False
        )
        self._is_dual_line: bool = False  # updated when first DLReport arrives

        # BLE client
        self._client: BleakClient | None = None
        self._connected = False
        self._write_char_uuid: str | None = None  # set after service discovery

        # Protocol handlers (created fresh on each connection)
        self._gen1_assembler: Gen1FrameAssembler | None = None
        self._gen2_framer: Gen2PacketFramer | None = None
        self._gen2_builder: Gen2CommandBuilder | None = None

        # State
        self.state: HughesState | None = None
        self._first_data_received = False

        # Health / timing
        self._last_data_time: float = 0.0
        self._watchdog_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_failures: int = 0
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return True if BLE connection is active."""
        return self._connected

    @property
    def generation(self) -> str:
        """Return the detected generation ('gen1' or 'gen2')."""
        return self._generation

    @property
    def is_enhanced(self) -> bool:
        """Return True if this is a Gen2 enhanced model (E8/V8/E9/V9)."""
        return self._is_enhanced

    @property
    def is_dual_line(self) -> bool:
        """Return True once dual-line operation is confirmed by device data."""
        return self._is_dual_line

    @property
    def address(self) -> str:
        """Return the BLE address."""
        return self._address

    @property
    def device_name(self) -> str:
        """Return the device name from config."""
        return self._device_name

    @property
    def data_healthy(self) -> bool:
        """Return True if connected and receiving fresh data."""
        if not self._connected or self.state is None:
            return False
        if self._last_data_time == 0.0:
            return False
        return (time.monotonic() - self._last_data_time) < STALE_TIMEOUT

    @property
    def last_data_age(self) -> float | None:
        """Seconds since last data, or None if never received."""
        if self._last_data_time == 0.0:
            return None
        return time.monotonic() - self._last_data_time

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    async def async_connect(self) -> None:
        """Establish BLE connection and run generation-specific init."""
        async with self._connect_lock:
            if self._connected:
                return
            await self._do_connect()

    async def _do_connect(self) -> None:
        """Internal connect logic."""
        _LOGGER.info(
            "Connecting to Hughes %s (%s, %s)",
            self._device_name or self._address,
            self._address,
            self._generation,
        )

        device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if device is None:
            _LOGGER.warning("Hughes device %s not found in BLE scan", self._address)
            self._schedule_reconnect()
            return

        try:
            client = await establish_connection(
                BleakClient,
                device,
                self._address,
                disconnected_callback=self._on_disconnect,
            )
        except (BleakError, TimeoutError, OSError) as exc:
            _LOGGER.warning("BLE connect failed: %s — will retry", exc)
            self._schedule_reconnect()
            return

        self._client = client
        self._connected = True
        self._reconnect_failures = 0
        _LOGGER.info("Connected to Hughes %s", self._address)

        # Short delay before service discovery
        await asyncio.sleep(SERVICE_DISCOVERY_DELAY)

        # Run generation-specific init
        try:
            if self._generation == GEN1:
                ok = await self._init_gen1(client)
            else:
                ok = await self._init_gen2(client)
        except (BleakError, TimeoutError, OSError) as exc:
            _LOGGER.error("Hughes %s init failed: %s", self._address, exc)
            await self._safe_disconnect(client)
            self._schedule_reconnect()
            return

        if not ok:
            await self._safe_disconnect(client)
            self._schedule_reconnect()
            return

        self._start_watchdog()
        self.async_update_listeners()

    async def _init_gen1(self, client: BleakClient) -> bool:
        """Initialize Gen1 connection: find service, enable notifications."""
        services = client.services
        svc = services.get_service(GEN1_SERVICE_UUID)
        if svc is None:
            _LOGGER.error(
                "Gen1 service %s not found on %s — wrong device or generation",
                GEN1_SERVICE_UUID,
                self._address,
            )
            return False

        notify_char = svc.get_characteristic(GEN1_NOTIFY_CHAR_UUID)
        if notify_char is None:
            _LOGGER.error("Gen1 notify characteristic %s not found", GEN1_NOTIFY_CHAR_UUID)
            return False

        await asyncio.sleep(OPERATION_DELAY)

        self._gen1_assembler = Gen1FrameAssembler()

        await client.start_notify(notify_char, self._on_gen1_notification)
        _LOGGER.info("Gen1 notifications enabled on %s", GEN1_NOTIFY_CHAR_UUID)
        return True

    async def _init_gen2(self, client: BleakClient) -> bool:
        """Initialize Gen2 connection: find service, enable notifications, send open command."""
        # Best-effort MTU negotiation — packet framer handles any MTU transparently
        try:
            mtu = await client.get_mtu_size()
            _LOGGER.debug("Hughes Gen2 %s: current MTU = %d", self._address, mtu)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Hughes Gen2: MTU query not supported on this adapter")

        services = client.services
        svc = services.get_service(GEN2_SERVICE_UUID)
        if svc is None:
            _LOGGER.error(
                "Gen2 service %s not found on %s — wrong device or generation",
                GEN2_SERVICE_UUID,
                self._address,
            )
            return False

        rw_char = svc.get_characteristic(GEN2_RW_CHAR_UUID)
        if rw_char is None:
            _LOGGER.error("Gen2 R/W characteristic %s not found", GEN2_RW_CHAR_UUID)
            return False

        self._write_char_uuid = GEN2_RW_CHAR_UUID
        self._gen2_framer = Gen2PacketFramer()
        self._gen2_builder = Gen2CommandBuilder()

        await asyncio.sleep(OPERATION_DELAY)

        # Enable notifications
        await client.start_notify(rw_char, self._on_gen2_notification)
        _LOGGER.info("Gen2 notifications enabled on %s", GEN2_RW_CHAR_UUID)

        # Send protocol open command
        try:
            await client.write_gatt_char(GEN2_RW_CHAR_UUID, GEN2_PROTOCOL_OPEN_CMD, response=True)
            _LOGGER.debug("Sent Gen2 protocol open command")
        except (BleakError, TimeoutError, OSError) as exc:
            _LOGGER.warning("Gen2 open command write failed: %s (continuing)", exc)

        await asyncio.sleep(GEN2_INIT_SETTLE_DELAY)
        return True

    async def async_disconnect(self) -> None:
        """Disconnect and clean up."""
        self._stop_watchdog()
        self._cancel_reconnect()
        if self._client:
            await self._safe_disconnect(self._client)
        self._client = None
        self._connected = False
        self._write_char_uuid = None
        self._gen1_assembler = None
        self._gen2_framer = None
        self._gen2_builder = None
        self.async_update_listeners()

    async def _safe_disconnect(self, client: BleakClient) -> None:
        """Disconnect silently, ignoring errors."""
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    @callback
    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected BLE disconnection."""
        _LOGGER.warning("Hughes %s disconnected", self._address)
        self._stop_watchdog()
        self._connected = False
        self._client = None
        self._write_char_uuid = None
        self._gen1_assembler = None
        self._gen2_framer = None
        self._gen2_builder = None
        self.async_update_listeners()
        self._schedule_reconnect()

    # ------------------------------------------------------------------
    # Reconnection
    # ------------------------------------------------------------------

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt with exponential backoff."""
        self._cancel_reconnect()
        self._reconnect_failures += 1
        delay = min(
            RECONNECT_BACKOFF_BASE * (2 ** (self._reconnect_failures - 1)),
            RECONNECT_BACKOFF_CAP,
        )
        _LOGGER.info(
            "Hughes %s: reconnecting in %.0fs (attempt %d)",
            self._address,
            delay,
            self._reconnect_failures,
        )
        self._reconnect_task = self._entry.async_create_background_task(
            self.hass, self._reconnect_after(delay), "hughes_reconnect"
        )

    async def _reconnect_after(self, delay: float) -> None:
        """Wait then reconnect."""
        await asyncio.sleep(delay)
        try:
            await self.async_connect()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Hughes reconnect failed")

    def _cancel_reconnect(self) -> None:
        """Cancel any pending reconnect task."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        """Start the connection health watchdog."""
        self._stop_watchdog()
        self._watchdog_task = self._entry.async_create_background_task(
            self.hass, self._watchdog_loop(), "hughes_watchdog"
        )

    def _stop_watchdog(self) -> None:
        """Stop the watchdog task."""
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        """Periodically check for stale data and force reconnect if needed."""
        try:
            while self._connected:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                if not self._connected:
                    break
                if self._last_data_time > 0.0:
                    age = time.monotonic() - self._last_data_time
                    if age > STALE_TIMEOUT:
                        _LOGGER.warning(
                            "Hughes %s: no data for %.0fs — forcing reconnect", self._address, age
                        )
                        if self._client:
                            await self._safe_disconnect(self._client)
                        break
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Notification handlers
    # ------------------------------------------------------------------

    def _on_gen1_notification(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle a 20-byte Gen1 notification chunk."""
        if self._gen1_assembler is None:
            return
        result = self._gen1_assembler.feed(bytes(data))
        if result is None:
            return
        line_data, is_line2 = result
        self._last_data_time = time.monotonic()
        self.hass.async_create_task(self._update_gen1_state(line_data, is_line2))

    async def _update_gen1_state(self, line_data: HughesLineData, is_line2: bool) -> None:
        """Merge parsed Gen1 frame into coordinator state and notify entities."""
        if self.state is None:
            self.state = HughesState(
                generation=GEN1,
                is_enhanced=False,
                is_dual_line=False,
            )

        if is_line2:
            self.state.is_dual_line = True
            self._is_dual_line = True
            self.state.line2 = line_data
        else:
            self.state.line1 = line_data

        self.state.last_seen = self._last_data_time

        if not self._first_data_received and not is_line2:
            self._first_data_received = True
            _LOGGER.info(
                "First Gen1 data from %s: %.2fV / %.4fA / error=%s",
                self._address,
                line_data.voltage,
                line_data.current,
                line_data.error_text,
            )

        self.async_set_updated_data(self.state)

    def _on_gen2_notification(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle a Gen2 BLE notification chunk."""
        if self._gen2_framer is None:
            return

        # Check for ASCII "ok" init response (before binary framing starts)
        if not self._first_data_received:
            try:
                text = bytes(data).decode("ascii", errors="ignore").strip()
                if text == "ok":
                    _LOGGER.debug("Hughes Gen2 %s: received protocol open 'ok'", self._address)
                    return
            except Exception:  # noqa: BLE001
                pass

        packets = self._gen2_framer.feed(bytes(data))
        for packet in packets:
            self._last_data_time = time.monotonic()
            if packet.command == GEN2_CMD_DL_REPORT:
                self.hass.async_create_task(self._update_gen2_state(packet.body))
            else:
                _LOGGER.debug(
                    "Hughes Gen2 %s: unhandled command 0x%02X (msg_id=%d)",
                    self._address,
                    packet.command,
                    packet.msg_id,
                )

    async def _update_gen2_state(self, body: bytes) -> None:
        """Parse a Gen2 DLReport body and push state to entities."""
        result = parse_dl_report(body, self._is_enhanced)
        if result is None:
            return

        line1, line2 = result
        is_dual = line2 is not None

        if is_dual and not self._is_dual_line:
            _LOGGER.info("Hughes Gen2 %s: dual-line device confirmed", self._address)
            self._is_dual_line = True

        self.state = HughesState(
            generation=GEN2,
            is_enhanced=self._is_enhanced,
            is_dual_line=is_dual,
            line1=line1,
            line2=line2,
            last_seen=self._last_data_time,
            raw_bytes=body,
        )

        if not self._first_data_received:
            self._first_data_received = True
            _LOGGER.info(
                "First Gen2 data from %s: %.4fV / %.4fA / relay=%s / error=%s",
                self._address,
                line1.voltage,
                line1.current,
                line1.relay_on,
                line1.error_text,
            )

        self.async_set_updated_data(self.state)

    # ------------------------------------------------------------------
    # Gen2 command methods
    # ------------------------------------------------------------------

    async def _send_gen2(self, payload: bytes) -> None:
        """Write a pre-built Gen2 command packet to the device."""
        if not self._client or not self._connected or not self._write_char_uuid:
            _LOGGER.warning("Hughes %s: cannot send command — not connected", self._address)
            return
        if self._generation != GEN2:
            _LOGGER.warning("Hughes %s: command only valid for Gen2 devices", self._address)
            return
        try:
            await self._client.write_gatt_char(
                self._write_char_uuid, payload, response=True
            )
        except (BleakError, TimeoutError, OSError) as exc:
            _LOGGER.error("Hughes %s: command write failed: %s", self._address, exc)
            raise

    async def async_set_relay(self, on: bool) -> None:
        """Turn relay on or off (Gen2 only)."""
        if self._gen2_builder is None:
            return
        _LOGGER.info("Hughes %s: setting relay %s", self._address, "ON" if on else "OFF")
        await self._send_gen2(self._gen2_builder.set_relay(on))

    async def async_set_backlight(self, level: int) -> None:
        """Set display backlight level 0–5 (Gen2 only)."""
        if self._gen2_builder is None:
            return
        _LOGGER.info("Hughes %s: setting backlight to %d", self._address, level)
        await self._send_gen2(self._gen2_builder.set_backlight(level))

    async def async_set_neutral_detection(self, enable: bool) -> None:
        """Enable or disable neutral detection (Gen2 only)."""
        if self._gen2_builder is None:
            return
        _LOGGER.info(
            "Hughes %s: neutral detection %s", self._address, "enabled" if enable else "disabled"
        )
        await self._send_gen2(self._gen2_builder.set_neutral_detection(enable))

    async def async_reset_energy(self) -> None:
        """Reset cumulative energy counter (Gen2 only)."""
        if self._gen2_builder is None:
            return
        _LOGGER.info("Hughes %s: resetting energy counter", self._address)
        await self._send_gen2(self._gen2_builder.energy_reset())

    async def async_sync_time(self) -> None:
        """Sync current UTC time to device (Gen2 only)."""
        if self._gen2_builder is None:
            return
        _LOGGER.info("Hughes %s: syncing time", self._address)
        await self._send_gen2(self._gen2_builder.set_time())

    # ------------------------------------------------------------------
    # DataUpdateCoordinator required method
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> HughesState | None:
        """Return the latest state (updates are BLE notification-driven, not HA-driven)."""
        return self.state
