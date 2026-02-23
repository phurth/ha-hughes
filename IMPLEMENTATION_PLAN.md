# ha-hughes: Hughes Power Watchdog — HA Custom Integration Implementation Plan

**Generated:** 2026-02-23
**Source of truth:** `android_ble_plugin_bridge/docs/INTERNALS.md`, sibling integrations `ha-onecontrol`, `ha-gopower`, `ha-mopeka`
**Target repo:** `github.com/phurth/ha-hughes`

---

## 1. Overview and Scope

### What this integration does

Provides native Home Assistant support for **Hughes Power Watchdog** surge protectors and power management units, connecting via BLE (either directly attached Bluetooth adapter or BT proxy).

Two distinct hardware generations exist with fundamentally different BLE protocols:

| | Gen1 | Gen2 |
|---|---|---|
| Device names | `PMD*`, `PWS*` | `WD_{model}_{serial}` |
| Hardware models | E2–E4 | E5–E9, V5–V9 |
| BLE service UUID | `0000FFE0-...` | `000000FF-...` |
| Protocol | Raw 40-byte frame (2×20-byte notifications) | Framed binary + ASCII init handshake |
| Authentication | None | None (protocol init only) |
| Pairing required | No | No |
| Commands | None (read-only; see §7.2) | Relay, backlight, neutral detection, time sync, energy reset |
| Enhanced sensors | No | Yes (E8/V8/E9/V9 only): output voltage, boost, temperature |
| Dual-line support | Yes (detected at runtime) | Yes (detected from DLReport body size) |

### Integration identity

- **Domain:** `hughes` (consistent with sibling naming: `onecontrol`, `gopower`, `mopeka`)
- **HACS category:** Integration
- **IoT class:** `local_push` (notifications drive state; no polling required)
- **Remote:** `github.com/phurth/ha-hughes`

---

## 2. Architecture Decisions

### 2.1 Single domain for both generations

Gen1 and Gen2 are treated as variants within one HA integration rather than separate integrations. Generation is auto-detected at connection time from device name:
- Name starts with `WD_` → Gen2
- Name starts with `PMD` or `PWS`, or unknown prefix → Gen1

This is transparent to the user; the config flow presents a single unified setup regardless of device generation.

### 2.2 Single coordinator with protocol subpackage

One `HughesCoordinator` class manages BLE lifecycle (connect, disconnect, reconnect, health tracking). Generation-specific protocol logic lives in `protocol/gen1.py` and `protocol/gen2.py`. The coordinator delegates to whichever is appropriate after detection.

This mirrors the gen-branching pattern from the Android plugin architecture (two plugin classes, one framework) while keeping the HA coordinator surface area minimal.

### 2.3 No pairing required

Neither generation requires BLE bonding. This makes the connection model substantially simpler than `ha-onecontrol`. No D-Bus agent, no `ble_agent.py`, no bonding PIN in config. Connect → notify → receive data.

### 2.4 Gen1 is read-only in v1

INTERNALS.md documents Gen1 command support (characteristic `0xFFF5`) as "Phase 2 — not yet implemented" with command payloads not yet captured. Gen1 will be read-only in the initial HA implementation. Gen2 has full command support from day one.

### 2.5 Dual-line as optional entities

50A devices (and some Gen2 devices) may send data for two lines (L1 + L2). The integration always creates both L1 and L2 entity sets. L2 entities report `available = False` until the coordinator receives a dual-line data frame, at which point L2 becomes available. This avoids requiring the user to specify amperage/line count during setup.

### 2.6 Enhanced model detection (Gen2)

Gen2 device names encode the model type: `WD_{model}_{serial}`. Models E8, V8, E9, V9 include additional sensors (output voltage, boost flag, temperature in °F). Enhanced entities are created for all Gen2 devices and become available only when the model is confirmed as enhanced. Detection is reliable from the first DLReport: non-enhanced devices send zero for output voltage and undefined boost/temperature fields, but the coordinator uses the model prefix from the device name to gate entity availability rather than sniffing field values.

### 2.7 Reconnection strategy

Consistent with `ha-gopower` and `ha-onecontrol`: exponential backoff, 5s base, 2× multiplier, 120s cap. Stale data timeout of 300 seconds triggers forced reconnect (no data received in 5 minutes).

---

## 3. File Structure

```
ha-hughes/
├── IMPLEMENTATION_PLAN.md               ← this file
├── hacs.json
└── custom_components/
    └── hughes/
        ├── __init__.py                  # Entry setup, platform forward, unload
        ├── manifest.json                # Domain, BLE matchers, requirements, version
        ├── const.py                     # All UUIDs, command codes, error maps, model lists, timing
        ├── models.py                    # HughesState, HughesLineData dataclasses; generation/model enums
        ├── config_flow.py               # BT discovery → confirm (no user-configured secrets)
        ├── coordinator.py               # HughesCoordinator: BLE lifecycle, generation dispatch, health
        ├── protocol/
        │   ├── __init__.py
        │   ├── gen1.py                  # Gen1: frame assembly (2×20), frame parser
        │   └── gen2.py                  # Gen2: MTU request, init handshake, packet framer, command builder
        ├── sensor.py                    # Voltage, current, power, energy, frequency, error, temp (enhanced), output_v (enhanced)
        ├── binary_sensor.py             # Data healthy, boost active (enhanced Gen2)
        ├── switch.py                    # Relay on/off (Gen2), neutral detection enable (Gen2)
        ├── number.py                    # Backlight level 0–5 (Gen2)
        ├── button.py                    # Energy reset (Gen2), time sync (Gen2)
        ├── diagnostics.py               # Full state dump for issue reporting
        ├── strings.json                 # Config flow and entity strings
        └── translations/
            └── en.json
```

---

## 4. Protocol Implementation Detail

### 4.1 Gen1 (`protocol/gen1.py`)

**BLE services/characteristics:**
- Service: `0000FFE0-0000-1000-8000-00805f9b34fb`
- Notify characteristic: `0000FFE2-0000-1000-8000-00805f9b34fb`
- Write characteristic (Phase 2): `0000FFF5-0000-1000-8000-00805f9b34fb` (not used in v1)
- CCCD: `00002902-0000-1000-8000-00805f9b34fb`

**Connection sequence:**
1. Connect via `bleak_retry_connector.establish_connection()`
2. 200ms delay after connection before service discovery
3. Discover service `0xFFE0`
4. Enable notifications on `0xFFE2` (subscribe + write CCCD `0x0100`)
5. Begin receiving 20-byte notification chunks

**Frame assembly logic (`Gen1FrameAssembler`):**
- Maintain `chunk1: bytes | None` and `chunk1_time: float`
- On notification: if `chunk1` is None or `(now - chunk1_time) > 1.0s`, store as `chunk1`; otherwise concatenate with `chunk1` to form 40-byte frame
- Validate: first three bytes of assembled frame must be `0x01 0x03 0x20`
- On valid frame: parse and return `HughesLineData`
- On invalid header: discard, reset `chunk1`

**Frame parsing (`parse_gen1_frame(frame: bytes)`):**
```
frame[3:7]   → input_voltage  (int32 big-endian, ÷ 10000)
frame[7:11]  → current        (int32 big-endian, ÷ 10000)
frame[11:15] → power          (int32 big-endian, ÷ 10000)
frame[15:19] → energy         (int32 big-endian, ÷ 10000, kWh)
frame[19]    → error_code     (uint8)
frame[31:35] → frequency      (int32 big-endian, ÷ 100)
frame[37:40] → line_marker    (all 0x00 = L1; all non-zero = L2)
```

**Error code map (Gen1):**
```
0=OK, 1=Overvoltage L1, 2=Overvoltage L2, 3=Undervoltage L1, 4=Undervoltage L2,
5=Overcurrent L1, 6=Overcurrent L2, 7=Hot/Neutral Reversed, 8=Lost Ground, 9=No RV Neutral
```

---

### 4.2 Gen2 (`protocol/gen2.py`)

**BLE services/characteristics:**
- Service: `000000FF-0000-1000-8000-00805f9b34fb`
- R/W/Notify characteristic: `0000FF01-0000-1000-8000-00805f9b34fb`
- CCCD: `00002902-0000-1000-8000-00805f9b34fb`

**Connection sequence:**
1. Connect via `bleak_retry_connector.establish_connection()`
2. Request MTU 80 (via `client.get_services()` → `BleakClient._backend` MTU negotiation; platform-specific)
3. Discover service `0x00FF`
4. Enable notifications on `0xFF01`
5. Write ASCII string `!%!%,protocol,open,` to `0xFF01` to enter binary mode
6. Receive `ok` acknowledgment (tolerate if missing)
7. Begin receiving binary framed packets

**Packet format:**
```
[0–3]    magic: 0x24 0x7C 0x27 0x40     (4 bytes, constant)
[4]      version: 0x01
[5]      msg_id: 1–100 rolling
[6]      command: byte (see command map)
[7–8]    data_len: uint16 big-endian
[9..N]   body: data_len bytes
[N+1–2]  tail: 0x71 0x21                (2 bytes, constant)
```

**Packet framer (`Gen2PacketFramer`):**
- Maintain internal byte buffer
- Append incoming notification bytes
- Scan buffer for magic `0x247C2740`; discard bytes before it
- Once magic found: check if buffer has enough bytes for header (9 bytes) + `data_len` body + 2 tail bytes
- If complete: validate tail `0x7121`, extract and return `Gen2Packet`; trim buffer
- If incomplete: wait for more data

**Command codes:**
```python
CMD_DL_REPORT         = 0x01  # device → host; real-time telemetry
CMD_ERROR_REPORT      = 0x02  # device → host; error history
CMD_ENERGY_RESET      = 0x03  # host → device; no body
CMD_ENERGY_RESTART    = 0x04  # host → device; no body
CMD_ERROR_DEL         = 0x05  # host → device; 1-byte record ID
CMD_SET_TIME          = 0x06  # host → device; 6-byte: Y-2000, M, D, H, Min, Sec
CMD_SET_BACKLIGHT     = 0x07  # host → device; 1-byte level 0–5
CMD_READ_START_TIME   = 0x08  # host → device; no body
CMD_SET_INIT_DATA     = 0x0A  # host → device; 15-byte (not exposed in v1)
CMD_SET_OPEN          = 0x0B  # host → device; 1=ON, 2=OFF
CMD_NEUTRAL_DETECTION = 0x0D  # host → device; 1=enable, 0=disable
CMD_ALARM             = 0x0E  # device → host; notification
```

**DLReport body parsing (`parse_dl_report(body: bytes)`):**
- `len(body) == 34` → single-line device; parse one 34-byte block
- `len(body) == 68` → dual-line device; parse two 34-byte blocks
```
block[0:4]   → input_voltage   (int32 big-endian, ÷ 10000)
block[4:8]   → current         (int32 big-endian, ÷ 10000)
block[8:12]  → power           (int32 big-endian, ÷ 10000)
block[12:16] → energy          (int32 big-endian, ÷ 10000, kWh)
block[16:20] → temp1 (internal; not directly exposed)
block[20:24] → output_voltage  (int32 big-endian, ÷ 10000; enhanced only)
block[24]    → backlight        (0–5)
block[25]    → neutral_detect   (0/1)
block[26]    → boost            (0/1; enhanced only)
block[27]    → temperature_f    (uint8, Fahrenheit; enhanced only)
block[28:32] → frequency        (int32 big-endian, ÷ 100)
block[32]    → error_code       (0–14)
block[33]    → relay_status     (1=ON, 2=OFF)
```

**Error code map (Gen2):**
```
0=OK, 1=Voltage Error L1, 2=Voltage Error L2, 3=Over Current L1, 4=Over Current L2,
5=Neutral Reversed L1, 6=Neutral Reversed L2, 7=Missing Ground, 8=Neutral Missing,
9=Surge Protection Used Up, 10=E10, 11=Frequency Error L1, 12=Frequency Error L2,
13=F3, 14=F4
```

**Command building (`build_packet(cmd, body=None)`):**
- Maintain rolling `msg_id` (1–100, wraps to 1)
- Write magic `0x247C2740`, version `0x01`, `msg_id`, `cmd`, `data_len` (big-endian), body, tail `0x7121`
- Write complete packet to `0xFF01` characteristic

**Enhanced model detection:**
```python
ENHANCED_MODELS = {"E8", "V8", "E9", "V9"}

def is_enhanced_model(device_name: str) -> bool:
    parts = device_name.split("_")     # "WD_E8_ABC123" → ["WD", "E8", "ABC123"]
    return len(parts) >= 2 and parts[1].upper() in ENHANCED_MODELS
```

---

## 5. Data Models (`models.py`)

```python
from dataclasses import dataclass, field
from typing import Literal

Generation = Literal["gen1", "gen2"]

@dataclass
class HughesLineData:
    voltage: float                        # V
    current: float                        # A
    power: float                          # W
    energy: float                         # kWh
    frequency: float                      # Hz
    error_code: int
    error_text: str
    # Gen2 only
    relay_on: bool | None = None
    neutral_detection: bool | None = None
    backlight: int | None = None          # 0–5
    # Gen2 enhanced only
    output_voltage: float | None = None   # V
    boost: bool | None = None
    temperature_f: float | None = None    # °F

@dataclass
class HughesState:
    generation: Generation
    is_enhanced: bool                     # Gen2 E8/V8/E9/V9
    is_dual_line: bool
    line1: HughesLineData
    line2: HughesLineData | None = None
    last_seen: float = 0.0                # monotonic timestamp
```

---

## 6. Entity Model

### 6.1 Sensors (both generations unless noted)

All L2 variants are created unconditionally; they report `available = False` until the coordinator confirms dual-line mode.

| Entity | Unit | Device Class | State Class | Notes |
|---|---|---|---|---|
| L1 Voltage | V | voltage | measurement | |
| L1 Current | A | current | measurement | |
| L1 Power | W | power | measurement | |
| L1 Energy | kWh | energy | total_increasing | |
| L1 Frequency | Hz | frequency | measurement | |
| L1 Error | — | — | — | text; enum from error map |
| L1 Error Code | — | — | — | numeric; entity_category=diagnostic |
| L2 Voltage | V | voltage | measurement | available only if dual-line |
| L2 Current | A | current | measurement | available only if dual-line |
| L2 Power | W | power | measurement | available only if dual-line |
| L2 Energy | kWh | energy | total_increasing | available only if dual-line |
| L2 Frequency | Hz | frequency | measurement | available only if dual-line |
| L2 Error | — | — | — | available only if dual-line |
| Output Voltage L1 | V | voltage | measurement | Gen2 enhanced only |
| Temperature | °F | temperature | measurement | Gen2 enhanced only; native_unit=FAHRENHEIT |
| Backlight Level | — | — | — | Gen2 only; entity_category=diagnostic; read-only state (control via number entity) |

### 6.2 Binary Sensors

| Entity | Device Class | Entity Category | Notes |
|---|---|---|---|
| Data Healthy | connectivity | diagnostic | False if no data for 300s |
| Boost Active L1 | — | diagnostic | Gen2 enhanced only |
| Boost Active L2 | — | diagnostic | Gen2 enhanced dual-line only |

### 6.3 Switches (Gen2 only)

| Entity | Notes |
|---|---|
| Relay | Main power relay; CMD_SET_OPEN (0x01=ON, 0x02=OFF) |
| Neutral Detection | CMD_NEUTRAL_DETECTION (0x01=enable, 0x00=disable) |

### 6.4 Number (Gen2 only)

| Entity | Range | Step | Unit | Notes |
|---|---|---|---|---|
| Backlight | 0–5 | 1 | — | CMD_SET_BACKLIGHT; entity_category=config |

### 6.5 Buttons (Gen2 only)

| Entity | Command | Entity Category | Notes |
|---|---|---|---|
| Reset Energy | CMD_ENERGY_RESET (0x03) | config | Clears cumulative kWh counter |
| Sync Time | CMD_SET_TIME (0x06) | config | Sends current UTC time to device |

### 6.6 Diagnostics

`diagnostics.py` exposes full `HughesState` dump plus coordinator metadata (generation, is_enhanced, is_dual_line, last_seen age, connect count, reconnect count).

---

## 7. Config Flow (`config_flow.py`)

### 7.1 BLE Auto-discovery

The manifest registers matchers for both generation service UUIDs and known name prefixes. HA Bluetooth triggers `async_step_bluetooth()` when a matching device is seen.

```json
"bluetooth": [
  {"service_uuid": "0000ffe0-0000-1000-8000-00805f9b34fb"},
  {"service_uuid": "000000ff-0000-1000-8000-00805f9b34fb"},
  {"local_name": "PMD*"},
  {"local_name": "PWS*"},
  {"local_name": "WD_*"}
]
```

**Note on UUID collision risk:** Service UUID `0000FFE0` and `000000FF` are short-form UUIDs from the Bluetooth SIG reserved range and may be shared by other manufacturers' devices. Name-based matching is the more reliable discriminator. The coordinator should validate the expected service UUID during GATT discovery and abort if not found.

### 7.2 Flow steps

**`async_step_bluetooth(discovery_info)`:**
- Extract BLE address and device name
- Determine generation from name
- Set unique_id from BLE address
- Abort if already configured
- Proceed to `async_step_confirm`

**`async_step_user()`:**
- Manual fallback: show list of discovered Hughes devices
- User selects one; proceed to `async_step_confirm`

**`async_step_confirm()`:**
- Show device name, detected generation, BLE address
- No user-configurable secrets (no PIN, no auth)
- Optional advanced field: **Preferred temperature unit** (°C / °F) for enhanced Gen2 temperature sensor (default: follow HA system setting)
- Create config entry with: `CONF_ADDRESS`, `detected_generation`, `device_name`

### 7.3 Options flow

Post-setup options (editable after config entry created):
- Temperature unit preference (°C / °F)
- No other user-configurable options for v1

---

## 8. Coordinator (`coordinator.py`)

### 8.1 Initialization

```python
class HughesCoordinator(DataUpdateCoordinator[HughesState | None]):
    def __init__(self, hass, config_entry):
        # Store address, device_name, detected_generation
        # Initialize state = None
        # Initialize reconnect backoff state
        # Set update_interval = None  (push-driven)
```

### 8.2 Connection lifecycle

```
async_setup()
  → try HA Bluetooth device (preferred, works with proxies)
  → fallback: direct HCI adapters hci0–hci3
  → establish_connection() via bleak_retry_connector
  → on connect: run generation-specific init sequence
  → register notification callback
  → start health watchdog task (300s stale check)

on notification:
  → delegate to Gen1FrameAssembler or Gen2PacketFramer
  → on complete parsed state: update coordinator data, async_set_updated_data()
  → reset last_seen timestamp

on disconnect:
  → set state.connected = False (or state = None if first connection never succeeded)
  → schedule reconnect with backoff
  → health watchdog detects and fires availability=False for entities

async_teardown()
  → cancel watchdog
  → disconnect BLE
```

### 8.3 Generation-specific init (invoked from coordinator after connection)

**Gen1 init:**
```
200ms delay
→ get_services()
→ find characteristic 0xFFE2
→ start_notify(0xFFE2, callback)
```

**Gen2 init:**
```
request MTU 80
→ get_services()
→ find characteristic 0xFF01
→ start_notify(0xFF01, callback)
→ write "!%!%,protocol,open," to 0xFF01
→ await "ok" response (3s timeout, non-fatal if missing)
```

### 8.4 Command methods (Gen2 only)

```python
async def async_set_relay(self, on: bool) -> None
async def async_set_backlight(self, level: int) -> None       # 0–5
async def async_set_neutral_detection(self, enable: bool) -> None
async def async_reset_energy(self) -> None
async def async_sync_time(self) -> None
```

All command methods build a Gen2 packet via `protocol/gen2.py`, write to `0xFF01`, and await the next DLReport to confirm state change (no explicit ACK from device for most commands).

---

## 9. manifest.json

```json
{
  "domain": "hughes",
  "name": "Hughes Power Watchdog",
  "codeowners": ["@petehurth"],
  "config_flow": true,
  "dependencies": ["bluetooth_adapters"],
  "bluetooth": [
    {"service_uuid": "0000ffe0-0000-1000-8000-00805f9b34fb"},
    {"service_uuid": "000000ff-0000-1000-8000-00805f9b34fb"},
    {"local_name": "PMD*"},
    {"local_name": "PWS*"},
    {"local_name": "WD_*"}
  ],
  "iot_class": "local_push",
  "requirements": [
    "bleak>=0.21.0",
    "bleak-retry-connector>=3.1.0"
  ],
  "version": "0.1.0"
}
```

---

## 10. Implementation Phases

### Phase 1 — Gen2 Read Path (core value, fastest to user)

Priority: **highest**. Gen2 is the current hardware; Gen1 is legacy. Implement in order:

1. `const.py` — all UUIDs, command codes, error maps, model detection constants
2. `models.py` — `HughesLineData`, `HughesState` dataclasses
3. `protocol/gen2.py` — packet framer + DLReport parser (no commands yet)
4. `coordinator.py` — BLE lifecycle for Gen2: connect, init sequence, notify, parse, reconnect
5. `config_flow.py` — BLE discovery → confirm
6. `__init__.py`, `manifest.json` — wiring and registration
7. `sensor.py` — L1/L2 sensors for Gen2 (all read sensors)
8. `binary_sensor.py` — data_healthy
9. `diagnostics.py`

**Acceptance:** Connect to a Gen2 device; all L1 voltage/current/power/energy/frequency/error sensors appear in HA and update in real time. Reconnect on BLE drop.

### Phase 2 — Gen2 Write Path

1. Add command methods to coordinator
2. `switch.py` — relay, neutral detection
3. `number.py` — backlight
4. `button.py` — energy reset, time sync

**Acceptance:** Relay can be toggled from HA. Backlight can be set. Energy reset and time sync buttons work.

### Phase 3 — Gen1 Read Path

1. `protocol/gen1.py` — frame assembler + frame parser
2. Extend coordinator to branch on generation
3. Extend `config_flow.py` to handle Gen1 detection
4. Verify sensor entities work for Gen1 (subset: no relay, no commands, no enhanced sensors)

**Acceptance:** Connect to a Gen1 device; L1 sensors appear and update.

### Phase 4 — Polish and HACS Readiness

1. Dual-line L2 entity validation with a real 50A device
2. Enhanced model validation (E8/V8 sensors: output voltage, boost, temperature)
3. `strings.json` / `translations/en.json` — all entity names and config flow strings
4. `hacs.json`
5. README with supported device models, setup instructions, BT proxy guidance
6. Icon and logo assets (follow Brands pipeline pattern from ha-onecontrol experience)

---

## 11. Known Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Service UUID `0xFFE0` / `0x00FF` are generic SIG short UUIDs shared by other devices | Validate expected service UUID in GATT after connect; abort cleanly if not found. Rely on name-prefix matching as primary discriminator. |
| Gen2 MTU negotiation on non-Linux (e.g., macOS dev) may silently use default MTU | Packet framer handles fragmented packets regardless of MTU. MTU 80 is a request, not a requirement for correctness. |
| Gen1 frame assembly: single chunk arriving without its pair produces a stale first-chunk indefinitely | 1-second timeout on `chunk1`; treat stale chunk as start of new frame. |
| Gen2 `!%!%,protocol,open,` init may not produce `ok` on some firmware versions | Make `ok` reception non-fatal; proceed with binary packet reception regardless. |
| Dual-line L2 entity availability: HA entity availability API requires careful coordination | L2 entities always created; `available` property gates on `coordinator.data.is_dual_line`. |
| Enhanced model false-negative: device doesn't advertise name with E8/V8/E9/V9 | Gate enhanced entities on coordinator model detection, not config-time; recheck on each successful DLReport if uncertain. |
| Short-UUID BLE service collision causing accidental connection to non-Hughes device | Name prefix check in coordinator immediately after connect (before enabling notify); abort if name doesn't match expected pattern. |

---

## 12. Protocol Reference Quick-Card

### Gen1 BLE UUIDs
```
Service:  0000FFE0-0000-1000-8000-00805f9b34fb
Notify:   0000FFE2-0000-1000-8000-00805f9b34fb
Write:    0000FFF5-0000-1000-8000-00805f9b34fb  (Phase 2, not used in v1)
CCCD:     00002902-0000-1000-8000-00805f9b34fb
```

### Gen2 BLE UUIDs
```
Service:  000000FF-0000-1000-8000-00805f9b34fb
R/W/Ntf:  0000FF01-0000-1000-8000-00805f9b34fb
CCCD:     00002902-0000-1000-8000-00805f9b34fb
```

### Gen2 Packet Magic / Tail
```
Magic: 0x24 0x7C 0x27 0x40
Tail:  0x71 0x21
```

### Gen2 Init Command
```
ASCII: !%!%,protocol,open,
```
