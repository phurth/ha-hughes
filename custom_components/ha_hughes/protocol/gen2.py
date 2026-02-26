"""Gen2 Hughes Power Watchdog protocol: packet framing and parsing.

Protocol overview:
  - Single R/W/Notify characteristic (0xFF01)
  - Connection sequence:
      1. Optional: request MTU 80
      2. Enable notifications on 0xFF01
      3. Write ASCII "!%!%,protocol,open," to enter binary mode
      4. Receive "ok" acknowledgment (non-fatal if absent)
      5. Receive binary framed packets (CMD_DL_REPORT etc.)
  - Packet format: magic(4) + version(1) + msg_id(1) + cmd(1) + data_len(2) + body(N) + tail(2)
  - No authentication or pairing required

Reference: Android HughesGen2DevicePlugin.kt / HughesGen2PacketFramer
"""

from __future__ import annotations

import datetime
import logging
import struct
from dataclasses import dataclass

from ..const import (
    GEN2_BACKLIGHT_MAX,
    GEN2_CMD_ENERGY_RESET,
    GEN2_CMD_NEUTRAL_DETECTION,
    GEN2_CMD_SET_BACKLIGHT,
    GEN2_CMD_SET_OPEN,
    GEN2_CMD_SET_TIME,
    GEN2_DLREPORT_DUAL_SIZE,
    GEN2_DLREPORT_SINGLE_SIZE,
    GEN2_ERROR_CODES,
    GEN2_HEADER_SIZE,
    GEN2_MAGIC,
    GEN2_MSG_ID_MAX,
    GEN2_OFF_BACKLIGHT,
    GEN2_OFF_BOOST,
    GEN2_OFF_CURRENT,
    GEN2_OFF_ENERGY,
    GEN2_OFF_ERROR_CODE,
    GEN2_OFF_FREQUENCY,
    GEN2_OFF_INPUT_VOLTAGE,
    GEN2_OFF_NEUTRAL_DETECT,
    GEN2_OFF_OUTPUT_VOLTAGE,
    GEN2_OFF_POWER,
    GEN2_OFF_RELAY_STATUS,
    GEN2_OFF_TEMPERATURE_F,
    GEN2_PROTOCOL_VERSION,
    GEN2_RELAY_OFF,
    GEN2_RELAY_ON,
    GEN2_SCALE_FREQ,
    GEN2_SCALE_POWER,
    GEN2_TAIL,
    GEN2_TAIL_SIZE,
)
from ..models import HughesLineData

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Packet data structure
# ---------------------------------------------------------------------------

@dataclass
class Gen2Packet:
    """A fully parsed Gen2 BLE packet."""

    msg_id: int
    command: int
    body: bytes


# ---------------------------------------------------------------------------
# Packet framer: reassembles fragmented BLE notifications into complete packets
# ---------------------------------------------------------------------------

class Gen2PacketFramer:
    """Reassembles Gen2 binary framed packets from BLE notification chunks.

    Usage:
        framer = Gen2PacketFramer()
        for packet in framer.feed(notification_data):
            handle(packet)
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes | bytearray) -> list[Gen2Packet]:
        """Append notification data and return any complete packets found."""
        self._buffer.extend(data)
        packets: list[Gen2Packet] = []

        while True:
            packet = self._try_extract()
            if packet is None:
                break
            packets.append(packet)

        return packets

    def _try_extract(self) -> Gen2Packet | None:
        """Try to extract one complete packet from the front of the buffer."""
        buf = self._buffer

        # Find magic header
        magic_idx = self._find_magic()
        if magic_idx < 0:
            # No magic found; keep last 3 bytes in case magic spans two notifications
            if len(buf) > 3:
                del self._buffer[: len(buf) - 3]
            return None

        if magic_idx > 0:
            _LOGGER.debug("Gen2 framer: discarding %d bytes before magic", magic_idx)
            del self._buffer[:magic_idx]
            buf = self._buffer

        # Need at least a full header
        if len(buf) < GEN2_HEADER_SIZE:
            return None

        # Check version
        if buf[4] != GEN2_PROTOCOL_VERSION:
            _LOGGER.debug("Gen2 framer: unexpected protocol version 0x%02X", buf[4])
            del self._buffer[:1]  # skip this magic byte and try again
            return None

        msg_id = buf[5]
        command = buf[6]
        data_len = struct.unpack_from(">H", buf, 7)[0]

        total_len = GEN2_HEADER_SIZE + data_len + GEN2_TAIL_SIZE
        if len(buf) < total_len:
            return None  # incomplete packet, wait for more data

        # Validate tail
        tail_start = GEN2_HEADER_SIZE + data_len
        tail = bytes(buf[tail_start: tail_start + GEN2_TAIL_SIZE])
        if tail != GEN2_TAIL:
            _LOGGER.debug(
                "Gen2 framer: bad tail %s (expected %s) — skipping",
                tail.hex(),
                GEN2_TAIL.hex(),
            )
            del self._buffer[:1]
            return None

        body = bytes(buf[GEN2_HEADER_SIZE: GEN2_HEADER_SIZE + data_len])
        del self._buffer[:total_len]

        return Gen2Packet(msg_id=msg_id, command=command, body=body)

    def _find_magic(self) -> int:
        """Return index of magic header in buffer, or -1 if not found."""
        buf = self._buffer
        for i in range(len(buf) - len(GEN2_MAGIC) + 1):
            if bytes(buf[i: i + len(GEN2_MAGIC)]) == GEN2_MAGIC:
                return i
        return -1

    def reset(self) -> None:
        """Discard all buffered data."""
        self._buffer.clear()


# ---------------------------------------------------------------------------
# DLReport parser
# ---------------------------------------------------------------------------

def _parse_int32_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">i", data, offset)[0]


def parse_dl_block(block: bytes, is_enhanced: bool) -> HughesLineData:
    """Parse one 34-byte DLReport data block into a HughesLineData."""
    voltage = _parse_int32_be(block, GEN2_OFF_INPUT_VOLTAGE) / GEN2_SCALE_POWER
    current = _parse_int32_be(block, GEN2_OFF_CURRENT) / GEN2_SCALE_POWER
    power = _parse_int32_be(block, GEN2_OFF_POWER) / GEN2_SCALE_POWER
    energy = _parse_int32_be(block, GEN2_OFF_ENERGY) / GEN2_SCALE_POWER
    frequency = _parse_int32_be(block, GEN2_OFF_FREQUENCY) / GEN2_SCALE_FREQ
    error_code = block[GEN2_OFF_ERROR_CODE]
    error_text = GEN2_ERROR_CODES.get(error_code, f"Unknown ({error_code})")
    relay_raw = block[GEN2_OFF_RELAY_STATUS]
    relay_on = relay_raw == GEN2_RELAY_ON  # 1=ON, 2=OFF, default ON for unknown
    neutral_detection = bool(block[GEN2_OFF_NEUTRAL_DETECT])
    backlight = int(block[GEN2_OFF_BACKLIGHT])

    output_voltage: float | None = None
    boost: bool | None = None
    temperature_f: float | None = None

    if is_enhanced:
        output_voltage = _parse_int32_be(block, GEN2_OFF_OUTPUT_VOLTAGE) / GEN2_SCALE_POWER
        boost = bool(block[GEN2_OFF_BOOST])
        temperature_f = float(block[GEN2_OFF_TEMPERATURE_F])

    return HughesLineData(
        voltage=round(voltage, 4),
        current=round(current, 4),
        power=round(power, 4),
        energy=round(energy, 4),
        frequency=round(frequency, 2),
        error_code=error_code,
        error_text=error_text,
        relay_on=relay_on,
        neutral_detection=neutral_detection,
        backlight=backlight,
        output_voltage=round(output_voltage, 4) if output_voltage is not None else None,
        boost=boost,
        temperature_f=temperature_f,
    )


def parse_dl_report(
    body: bytes, is_enhanced: bool
) -> tuple[HughesLineData, HughesLineData | None] | None:
    """Parse a DLReport body.

    Returns (line1, line2_or_None) or None on parse error.
    """
    if len(body) == GEN2_DLREPORT_SINGLE_SIZE:
        line1 = parse_dl_block(body, is_enhanced)
        return line1, None
    elif len(body) == GEN2_DLREPORT_DUAL_SIZE:
        line1 = parse_dl_block(body[:GEN2_DLREPORT_SINGLE_SIZE], is_enhanced)
        line2 = parse_dl_block(body[GEN2_DLREPORT_SINGLE_SIZE:], is_enhanced)
        return line1, line2
    else:
        _LOGGER.warning(
            "Gen2 DLReport unexpected body size: %d (expected %d or %d)",
            len(body),
            GEN2_DLREPORT_SINGLE_SIZE,
            GEN2_DLREPORT_DUAL_SIZE,
        )
        return None


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

class Gen2CommandBuilder:
    """Builds framed Gen2 command packets.

    Usage:
        builder = Gen2CommandBuilder()
        packet_bytes = builder.set_relay(True)
    """

    def __init__(self) -> None:
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id = (self._msg_id % GEN2_MSG_ID_MAX) + 1
        return self._msg_id

    def _build(self, cmd: int, body: bytes = b"") -> bytes:
        """Build a framed Gen2 packet."""
        msg_id = self._next_id()
        data_len = len(body)
        header = (
            GEN2_MAGIC
            + bytes([GEN2_PROTOCOL_VERSION, msg_id, cmd])
            + struct.pack(">H", data_len)
        )
        return header + body + GEN2_TAIL

    def set_relay(self, on: bool) -> bytes:
        """Build CMD_SET_OPEN packet (0x0B)."""
        return self._build(GEN2_CMD_SET_OPEN, bytes([GEN2_RELAY_ON if on else GEN2_RELAY_OFF]))

    def set_backlight(self, level: int) -> bytes:
        """Build CMD_SET_BACKLIGHT packet (0x07). level must be 0–5."""
        level = max(0, min(GEN2_BACKLIGHT_MAX, level))
        return self._build(GEN2_CMD_SET_BACKLIGHT, bytes([level]))

    def set_neutral_detection(self, enable: bool) -> bytes:
        """Build CMD_NEUTRAL_DETECTION packet (0x0D)."""
        return self._build(GEN2_CMD_NEUTRAL_DETECTION, bytes([0x01 if enable else 0x00]))

    def energy_reset(self) -> bytes:
        """Build CMD_ENERGY_RESET packet (0x03)."""
        return self._build(GEN2_CMD_ENERGY_RESET)

    def set_time(self, dt: datetime.datetime | None = None) -> bytes:
        """Build CMD_SET_TIME packet (0x06) using current UTC time."""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        year_offset = dt.year - 2000
        body = bytes([year_offset, dt.month, dt.day, dt.hour, dt.minute, dt.second])
        return self._build(GEN2_CMD_SET_TIME, body)
