"""Gen1 Hughes Power Watchdog protocol: frame assembly and parsing.

Protocol overview:
  - Device sends two consecutive 20-byte BLE notifications on characteristic FFE2
  - Each pair forms one 40-byte frame
  - No checksum; validate by checking the 3-byte header (0x01 0x03 0x20)
  - All multi-byte integers are big-endian, divide by 10,000 for engineering units
  - No authentication or pairing required

Reference: Android HughesWatchdogDevicePlugin.kt / HughesGattCallback
"""

from __future__ import annotations

import logging
import struct
import time

from ..const import (
    GEN1_CHUNK_SIZE,
    GEN1_CHUNK_TIMEOUT,
    GEN1_ERROR_CODES,
    GEN1_FRAME_HEADER,
    GEN1_FRAME_SIZE,
    GEN1_OFF_CURRENT,
    GEN1_OFF_ERROR,
    GEN1_OFF_ENERGY,
    GEN1_OFF_FREQUENCY,
    GEN1_OFF_LINE_MARKER,
    GEN1_OFF_POWER,
    GEN1_OFF_VOLTAGE,
    GEN1_SCALE_POWER,
)
from ..models import HughesLineData

_LOGGER = logging.getLogger(__name__)


def _parse_int32_be(data: bytes, offset: int) -> int:
    """Parse a big-endian signed int32 from data at offset."""
    return struct.unpack_from(">i", data, offset)[0]


def parse_gen1_frame(frame: bytes) -> tuple[HughesLineData, bool] | None:
    """Parse a complete 40-byte Gen1 frame.

    Returns (HughesLineData, is_line2) or None on parse error.
    The bool indicates whether marker bytes signal Line 2 (True) vs Line 1 (False).
    """
    if len(frame) < GEN1_FRAME_SIZE:
        _LOGGER.debug("Gen1 frame too short: %d bytes", len(frame))
        return None

    if frame[0:3] != GEN1_FRAME_HEADER:
        _LOGGER.debug(
            "Gen1 frame header mismatch: %s (expected %s)",
            frame[0:3].hex(),
            GEN1_FRAME_HEADER.hex(),
        )
        return None

    try:
        voltage = _parse_int32_be(frame, GEN1_OFF_VOLTAGE) / GEN1_SCALE_POWER
        current = _parse_int32_be(frame, GEN1_OFF_CURRENT) / GEN1_SCALE_POWER
        power = _parse_int32_be(frame, GEN1_OFF_POWER) / GEN1_SCALE_POWER
        energy = _parse_int32_be(frame, GEN1_OFF_ENERGY) / GEN1_SCALE_POWER
        frequency = _parse_int32_be(frame, GEN1_OFF_FREQUENCY) / 100.0
        error_code = frame[GEN1_OFF_ERROR]
        error_text = GEN1_ERROR_CODES.get(error_code, f"Unknown ({error_code})")

        # Line detection: bytes [37:40] all 0x00 = L1, any non-zero = L2
        marker = frame[GEN1_OFF_LINE_MARKER:GEN1_OFF_LINE_MARKER + 3]
        is_line2 = any(b != 0 for b in marker)

    except (struct.error, IndexError) as exc:
        _LOGGER.warning("Gen1 frame parse error: %s", exc)
        return None

    return (
        HughesLineData(
            voltage=round(voltage, 4),
            current=round(current, 4),
            power=round(power, 4),
            energy=round(energy, 4),
            frequency=round(frequency, 2),
            error_code=error_code,
            error_text=error_text,
        ),
        is_line2,
    )


class Gen1FrameAssembler:
    """Assembles 40-byte Gen1 frames from two sequential 20-byte BLE notifications.

    Usage:
        assembler = Gen1FrameAssembler()
        result = assembler.feed(notification_data)  # call on each notification
        if result is not None:
            line_data, is_line2 = result
    """

    def __init__(self) -> None:
        self._chunk1: bytes | None = None
        self._chunk1_time: float = 0.0

    def feed(self, data: bytes | bytearray) -> tuple[HughesLineData, bool] | None:
        """Feed a 20-byte notification chunk.

        Returns a parsed (HughesLineData, is_line2) tuple when a complete frame
        is assembled, or None if more data is needed.
        """
        chunk = bytes(data)

        if len(chunk) != GEN1_CHUNK_SIZE:
            _LOGGER.debug(
                "Unexpected Gen1 chunk size: %d (expected %d)", len(chunk), GEN1_CHUNK_SIZE
            )
            # Non-standard chunk: attempt to use as start of a new frame pair anyway
            self._chunk1 = chunk
            self._chunk1_time = time.monotonic()
            return None

        if self._chunk1 is None:
            # First chunk of a frame pair
            self._chunk1 = chunk
            self._chunk1_time = time.monotonic()
            _LOGGER.debug("Gen1: stored first chunk")
            return None

        # Check if first chunk has expired
        age = time.monotonic() - self._chunk1_time
        if age > GEN1_CHUNK_TIMEOUT:
            _LOGGER.debug(
                "Gen1: first chunk expired (%.2fs old) — treating current chunk as new first",
                age,
            )
            self._chunk1 = chunk
            self._chunk1_time = time.monotonic()
            return None

        # Assemble 40-byte frame
        frame = self._chunk1 + chunk
        self._chunk1 = None

        result = parse_gen1_frame(frame)
        if result is None:
            _LOGGER.debug("Gen1: frame parse failed — resetting")
        return result

    def reset(self) -> None:
        """Discard any buffered partial frame."""
        self._chunk1 = None
        self._chunk1_time = 0.0
