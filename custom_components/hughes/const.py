"""Constants for the Hughes Power Watchdog BLE integration."""

DOMAIN = "hughes"

# ---------------------------------------------------------------------------
# Config entry data keys
# ---------------------------------------------------------------------------
CONF_GENERATION = "generation"   # "gen1" or "gen2"
CONF_DEVICE_NAME = "device_name"

# ---------------------------------------------------------------------------
# Generation identifiers
# ---------------------------------------------------------------------------
GEN1 = "gen1"
GEN2 = "gen2"

# Gen2 enhanced models: these include output voltage, boost flag, temperature
GEN2_ENHANCED_MODELS = frozenset({"E8", "V8", "E9", "V9"})

# Device name prefixes used for BLE discovery and generation detection
GEN1_NAME_PREFIXES = ("PMD", "PWS")
GEN2_NAME_PREFIX = "WD_"

# ---------------------------------------------------------------------------
# Gen1 BLE UUIDs
# ---------------------------------------------------------------------------
GEN1_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
GEN1_NOTIFY_CHAR_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"
# Write characteristic (Phase 2 / future commands — not used in v1)
GEN1_WRITE_CHAR_UUID = "0000fff5-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# Gen2 BLE UUIDs
# ---------------------------------------------------------------------------
GEN2_SERVICE_UUID = "000000ff-0000-1000-8000-00805f9b34fb"
GEN2_RW_CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# All service UUIDs (used in config flow for discovery filtering)
# ---------------------------------------------------------------------------
ALL_SERVICE_UUIDS = (GEN1_SERVICE_UUID, GEN2_SERVICE_UUID)

# ---------------------------------------------------------------------------
# Gen1 protocol constants
# ---------------------------------------------------------------------------
GEN1_FRAME_SIZE = 40
GEN1_CHUNK_SIZE = 20
GEN1_FRAME_HEADER = bytes([0x01, 0x03, 0x20])
GEN1_CHUNK_TIMEOUT = 1.0   # seconds before first chunk is discarded

# Gen1 frame byte offsets
GEN1_OFF_VOLTAGE = 3
GEN1_OFF_CURRENT = 7
GEN1_OFF_POWER = 11
GEN1_OFF_ENERGY = 15
GEN1_OFF_ERROR = 19
GEN1_OFF_FREQUENCY = 31
GEN1_OFF_LINE_MARKER = 37

GEN1_SCALE_POWER = 10_000.0   # divide int32 by this to get engineering units

# Gen1 error codes
GEN1_ERROR_CODES: dict[int, str] = {
    0: "OK",
    1: "Overvoltage L1",
    2: "Overvoltage L2",
    3: "Undervoltage L1",
    4: "Undervoltage L2",
    5: "Overcurrent L1",
    6: "Overcurrent L2",
    7: "Hot/Neutral Reversed",
    8: "Lost Ground",
    9: "No RV Neutral",
}

# ---------------------------------------------------------------------------
# Gen2 protocol constants
# ---------------------------------------------------------------------------
GEN2_MTU_REQUEST = 80
GEN2_PROTOCOL_OPEN_CMD = b"!%!%,protocol,open,"
GEN2_PROTOCOL_OK_RESPONSE = b"ok"

# Packet framing
GEN2_MAGIC = bytes([0x24, 0x7C, 0x27, 0x40])
GEN2_TAIL = bytes([0x71, 0x21])
GEN2_PROTOCOL_VERSION = 0x01
GEN2_HEADER_SIZE = 9     # magic(4) + version(1) + msg_id(1) + cmd(1) + data_len(2)
GEN2_TAIL_SIZE = 2
GEN2_MSG_ID_MAX = 100

# Gen2 command codes
GEN2_CMD_DL_REPORT = 0x01
GEN2_CMD_ERROR_REPORT = 0x02
GEN2_CMD_ENERGY_RESET = 0x03
GEN2_CMD_ENERGY_RESTART = 0x04
GEN2_CMD_ERROR_DEL = 0x05
GEN2_CMD_SET_TIME = 0x06
GEN2_CMD_SET_BACKLIGHT = 0x07
GEN2_CMD_READ_START_TIME = 0x08
GEN2_CMD_SET_INIT_DATA = 0x0A
GEN2_CMD_SET_OPEN = 0x0B
GEN2_CMD_NEUTRAL_DETECTION = 0x0D
GEN2_CMD_ALARM = 0x0E

# Gen2 relay values
GEN2_RELAY_ON = 0x01
GEN2_RELAY_OFF = 0x02

# Gen2 DLReport body sizes
GEN2_DLREPORT_SINGLE_SIZE = 34
GEN2_DLREPORT_DUAL_SIZE = 68

# Gen2 DLReport block byte offsets (per 34-byte block)
GEN2_OFF_INPUT_VOLTAGE = 0
GEN2_OFF_CURRENT = 4
GEN2_OFF_POWER = 8
GEN2_OFF_ENERGY = 12
GEN2_OFF_TEMP1 = 16        # internal temp value (not directly exposed)
GEN2_OFF_OUTPUT_VOLTAGE = 20  # enhanced only (E8/V8+)
GEN2_OFF_BACKLIGHT = 24
GEN2_OFF_NEUTRAL_DETECT = 25
GEN2_OFF_BOOST = 26        # enhanced only
GEN2_OFF_TEMPERATURE_F = 27  # enhanced only
GEN2_OFF_FREQUENCY = 28
GEN2_OFF_ERROR_CODE = 32
GEN2_OFF_RELAY_STATUS = 33

GEN2_SCALE_POWER = 10_000.0
GEN2_SCALE_FREQ = 100.0

GEN2_BACKLIGHT_MAX = 5

# Gen2 error codes
GEN2_ERROR_CODES: dict[int, str] = {
    0: "OK",
    1: "Voltage Error L1",
    2: "Voltage Error L2",
    3: "Over Current L1",
    4: "Over Current L2",
    5: "Neutral Reversed L1",
    6: "Neutral Reversed L2",
    7: "Missing Ground",
    8: "Neutral Missing",
    9: "Surge Protection Used Up",
    10: "E10",
    11: "Frequency Error L1",
    12: "Frequency Error L2",
    13: "F3",
    14: "F4",
}

# ---------------------------------------------------------------------------
# Timing (seconds)
# ---------------------------------------------------------------------------
SERVICE_DISCOVERY_DELAY = 0.2
OPERATION_DELAY = 0.1
GEN2_INIT_TIMEOUT = 3.0        # wait for "ok" response after open command
GEN2_INIT_SETTLE_DELAY = 0.2   # pause after init before expecting packets

RECONNECT_BACKOFF_BASE = 5.0
RECONNECT_BACKOFF_CAP = 120.0
STALE_TIMEOUT = 300.0          # 5 min without data → force reconnect
WATCHDOG_INTERVAL = 60.0       # health-check interval
