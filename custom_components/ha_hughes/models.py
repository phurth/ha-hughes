"""Data models for the Hughes Power Watchdog BLE integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Generation = Literal["gen1", "gen2"]


@dataclass
class HughesLineData:
    """Parsed data for a single power line (L1 or L2)."""

    # Common — Gen1 and Gen2
    voltage: float = 0.0         # V
    current: float = 0.0         # A
    power: float = 0.0           # W
    energy: float = 0.0          # kWh
    frequency: float = 0.0       # Hz
    error_code: int = 0
    error_text: str = "OK"

    # Gen2 only
    relay_on: bool | None = None
    neutral_detection: bool | None = None
    backlight: int | None = None  # 0–5

    # Gen2 enhanced (E8/V8/E9/V9) only
    output_voltage: float | None = None  # V
    boost: bool | None = None
    temperature_f: float | None = None  # °F


@dataclass
class HughesState:
    """Full parsed state from a Hughes Power Watchdog device."""

    generation: Generation
    is_enhanced: bool         # Gen2 E8/V8/E9/V9 only
    is_dual_line: bool
    line1: HughesLineData = field(default_factory=HughesLineData)
    line2: HughesLineData | None = None
    last_seen: float = 0.0    # monotonic timestamp of last successful parse
    raw_bytes: bytes | None = None  # last raw payload for diagnostics
