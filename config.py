"""
Avionics configuration — pin assignments, thresholds, tuning.
Edit this file for your specific hardware setup.
"""

# ── Software Version ────────────────────────────────────────
VERSION = "1.6.0"

# ── Pin Assignments ──────────────────────────────────────────

# Barometer (GY-68 BMP180 — SoftI2C, hardware I2C has EIO bug on this board)
I2C_SDA = 4        # GP4 — ALT-DTA
I2C_SCL = 5        # GP5 — ALT-CLK
I2C_FREQ = 100_000
I2C_TIMEOUT_US = 50_000   # I2C timeout (BMP180 worst case ~26ms, 50ms gives margin)
BMP180_ADDR = 0x77  # GY-68 default address

# SD Card (SPI0)
SPI_ID = 0
SPI_MISO = 16      # GP16 — SD-SlaveOut
SPI_MOSI = 19      # GP19 — SD-SlaveIn
SPI_SCK = 18       # GP18 — SD-CLK
SPI_CS = 17        # GP17 — SD-ChipSelect
SPI_BAUD = 10_000_000  # 10 MHz

# ADC — voltage monitoring
ADC_V5 = 26        # GP26 (A0) — 5V rail, 500Ω/680Ω divider
ADC_V9 = 27        # GP27 (A1) — 9V rail, 2k/1k divider
ADC_V3 = 28        # GP28 (A2) — 3.3V rail, direct (no divider)
VREF = 3.3
ADC_RESOLUTION = 65535
# Voltage divider ratios: V_actual = V_adc * ratio
VDIV_3V = 1.0      # direct — 3.3V is within ADC range
VDIV_5V = 1.735    # voltage divider — R1∥R2 (500Ω) + R3 (680Ω), V_tap=2.88V
VDIV_9V = 3.0      # 2k/1k divider

# Indicators
LED_PIN = 25       # onboard LED (no external LED on board)

# ── Logging ──────────────────────────────────────────────────

SAMPLE_RATE_HZ = 25       # sensor read rate (Core 0)
LOG_FILENAME = "/sd/flight.bin"
LOG_FLUSH_EVERY = 25      # flush to SD every N frames (~1s at 25Hz)
LOG_SYNC_EVERY = 3        # os.sync() every N flushes (~3s at 25Hz/25 frames)

# ── MPR Altitude Logger Tuning ───────────────────────────────

# Kalman filter process/measurement noise
# Tune these on the ground: lower Q = smoother, higher R = trust model more
KALMAN_Q_ALT = 0.1        # process noise — altitude (m²)
KALMAN_Q_VEL = 0.5        # process noise — velocity (m²/s²)
KALMAN_R_ALT = 1.0        # measurement noise — barometric alt (m²)

# State machine thresholds
LAUNCH_ALT_THRESHOLD = 15.0    # altitude gain (m) required for launch detect
LAUNCH_VEL_THRESHOLD = 10.0   # velocity (m/s) required for launch detect
LAUNCH_DETECT_WINDOW = 0.5    # seconds — both thresholds must hold this long
BOOST_RECOVERY_ALT = 10.0     # if AGL drops below this in BOOST, reset to PAD (m)
BOOST_RECOVERY_WINDOW = 2.0   # seconds — recovery only possible within this window
COAST_VEL_THRESHOLD = 5.0     # velocity drop from peak to detect burnout
COAST_TIMEOUT = 30.0          # seconds — force apogee if stuck in COAST this long
APOGEE_VEL_THRESHOLD = 2.0    # |velocity| < this = apogee (m/s)
APOGEE_CONFIRM_COUNT = 5      # consecutive readings below threshold
APOGEE_DWELL_FRAMES = 5      # frames to stay in APOGEE before DROGUE (~200ms at 25Hz)
BOOST_RECOVERY_COUNT = 3     # consecutive frames below recovery alt before PAD reset
LANDED_VEL_THRESHOLD = 0.5    # near-zero velocity
LANDED_CONFIRM_SECONDS = 5.0  # must be still for this long
MAIN_CHUTE_FRACTION = 0.25    # DROGUE→MAIN at this fraction of max AGL (works for any apogee)

# Ground reference
GROUND_SAMPLES = 50           # samples to average for ground-level pressure


def validate():
    """Validate config values at startup. Raises ValueError on bad config."""
    errors = []
    if KALMAN_Q_ALT <= 0:
        errors.append("KALMAN_Q_ALT must be > 0")
    if KALMAN_Q_VEL <= 0:
        errors.append("KALMAN_Q_VEL must be > 0")
    if KALMAN_R_ALT <= 0:
        errors.append("KALMAN_R_ALT must be > 0")
    if not (1 <= SAMPLE_RATE_HZ <= 100):
        errors.append("SAMPLE_RATE_HZ must be 1-100")

    # Check for pin conflicts
    pins = {
        'I2C_SDA': I2C_SDA, 'I2C_SCL': I2C_SCL,
        'SPI_MISO': SPI_MISO, 'SPI_MOSI': SPI_MOSI,
        'SPI_SCK': SPI_SCK, 'SPI_CS': SPI_CS,
        'LED_PIN': LED_PIN,
    }
    seen = {}
    for name, pin in pins.items():
        if pin in seen:
            errors.append("Pin conflict: {} and {} both use GP{}".format(seen[pin], name, pin))
        seen[pin] = name

    if errors:
        raise ValueError("Config errors: " + "; ".join(errors))
