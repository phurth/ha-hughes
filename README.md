# ha-hughes

Home Assistant HACS integration for **Hughes Power Watchdog** surge protectors and power management devices.

Connects directly to the device over Bluetooth Low Energy — no cloud, no MQTT bridge, no internet required.

## Features

- **Auto-discovery** via BLE advertisements (name prefix `PMD*`, `PWS*`, or `WD_*`)
- **Real-time power monitoring**: voltage, current, power, energy, frequency
- **Dual-line (50A) support**: L1 and L2 entities created automatically when dual-line operation is detected
- **Gen1 and Gen2 device support**: generation auto-detected from device name
- **Gen2 control commands**: relay on/off, backlight level, neutral detection, energy reset, time sync
- **Diagnostics**: connection health binary sensor, raw frame dump, one-click diagnostics download

## Entities

### All devices (Gen1 and Gen2)

| Entity | Type | Device Class | Unit | Notes |
|--------|------|-------------|------|-------|
| L1 Voltage | Sensor | voltage | V | |
| L1 Current | Sensor | current | A | |
| L1 Power | Sensor | power | W | |
| L1 Energy | Sensor | energy | kWh | Total increasing |
| L1 Frequency | Sensor | frequency | Hz | |
| L1 Error | Sensor (diag) | — | — | Text description |
| L1 Error Code | Sensor (diag) | — | — | Numeric code |
| L2 Voltage | Sensor | voltage | V | Dual-line only |
| L2 Current | Sensor | current | A | Dual-line only |
| L2 Power | Sensor | power | W | Dual-line only |
| L2 Energy | Sensor | energy | kWh | Dual-line only |
| L2 Frequency | Sensor | frequency | Hz | Dual-line only |
| L2 Error | Sensor (diag) | — | — | Dual-line only |
| L2 Error Code | Sensor (diag) | — | — | Dual-line only |
| Connected | Binary Sensor (diag) | connectivity | — | |
| Data Healthy | Binary Sensor (diag) | problem | — | |

### Gen2 only

| Entity | Type | Notes |
|--------|------|-------|
| Relay | Switch | Main output relay |
| Neutral Detection | Switch | Enable/disable neutral monitoring |
| Backlight | Number | Display brightness (0–5) |
| Reset Energy | Button | Clear cumulative energy counter |
| Sync Time | Button | Push current UTC time to device |

### Gen2 enhanced models (E8, V8, E9, V9) only

| Entity | Type | Device Class | Unit |
|--------|------|-------------|------|
| L1 Output Voltage | Sensor | voltage | V |
| Temperature | Sensor | temperature | °F |
| L1 Boost Active | Binary Sensor (diag) | — | — |

## Requirements

- Home Assistant 2024.1+ with Bluetooth integration
- Bluetooth adapter on the HA host (or ESPHome BT proxy)
- Hughes Power Watchdog device within BLE range

## Installation (HACS)

1. Add this repository as a custom HACS repository
2. Install "Hughes Power Watchdog"
3. Restart Home Assistant
4. The device should auto-discover — or add manually via Settings → Devices & Services → Add Integration → Hughes Power Watchdog and enter the Bluetooth MAC address

## Protocol

### Gen1 (PMD\*, PWS\* name prefix)

- **Service**: `0000FFE0-0000-1000-8000-00805F9B34FB`
- **Notify** (`FFE2`): 20-byte chunks assembled in pairs into 40-byte frames
- Big-endian int32 values ÷ 10000 for electrical measurements
- No authentication or pairing required

### Gen2 (WD\_\* name prefix)

- **Service**: `000000FF-0000-1000-8000-00805F9B34FB`
- **Read/Write/Notify** (`FF01`): Binary framed packets with magic header `0x247C2740`
- ASCII handshake (`!%!%,protocol,open,` → `ok`) enters binary mode
- `DLReport` (0x01) carries 34-byte (single) or 68-byte (dual-line) measurement body
- No authentication or pairing required

## Debug Logging

```yaml
logger:
  logs:
    custom_components.hughes: debug
```

Use **Download diagnostics** from the integration page for a full runtime state dump including raw frame bytes (Gen2), connection health, and all parsed values.

## License

MIT
