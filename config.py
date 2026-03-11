"""
Avionics configuration — pin assignments, thresholds, tuning.
Edit this file for your specific hardware setup.
"""

# ── Pin Assignments ──────────────────────────────────────────

# Barometer (GY-68 BMP180 — SoftI2C, hardware I2C has EIO bug on this board)
I2C_SDA = 4        # GP4 — ALT-DTA
I2C_SCL = 5        # GP5 — ALT-CLK
I2C_FREQ = 100_000
BMP180_ADDR = 0x77  # GY-68 default address

# SD Card (SPI0)
SPI_ID = 0
SPI_MISO = 16      # GP16 — SD-SlaveOut
SPI_MOSI = 19      # GP19 — SD-SlaveIn
SPI_SCK = 18       # GP18 — SD-CLK
SPI_CS = 17        # GP17 — SD-ChipSelect
SPI_BAUD = 10_000_000  # 10 MHz

# ADC — voltage monitoring
ADC_V5 = 26        # GP26 (A0) — 5V rail, 1k/1k divider
ADC_V9 = 27        # GP27 (A1) — 9V rail, 2k/1k divider
ADC_V3 = 28        # GP28 (A2) — 3.3V rail, direct (no divider)
VREF = 3.3
ADC_RESOLUTION = 65535
# Voltage divider ratios: V_actual = V_adc * ratio
VDIV_3V = 1.0      # direct — 3.3V is within ADC range
VDIV_5V = 2.0      # voltage divider — see schematic
VDIV_9V = 3.0      # 2k/1k divider

# Indicators
LED_PIN = 25       # onboard LED (no external LED on board)

# ── Logging ──────────────────────────────────────────────────

SAMPLE_RATE_HZ = 25       # sensor read rate (Core 0)
LOG_FILENAME = "/sd/flight.bin"
LOG_FLUSH_EVERY = 25      # flush to SD every N frames (~1s at 25Hz)

# ── MPR Altitude Logger Tuning ───────────────────────────────

# Kalman filter process/measurement noise
# Tune these on the ground: lower Q = smoother, higher R = trust model more
KALMAN_Q_ALT = 0.1        # process noise — altitude (m²)
KALMAN_Q_VEL = 0.5        # process noise — velocity (m²/s²)
KALMAN_R_ALT = 1.0        # measurement noise — barometric alt (m²)

# State machine thresholds
LAUNCH_ACCEL_THRESHOLD = 2.0   # altitude gain (m) in detection window
LAUNCH_DETECT_WINDOW = 0.5    # seconds
COAST_VEL_THRESHOLD = 5.0     # velocity drop from peak to detect burnout
APOGEE_VEL_THRESHOLD = 2.0    # |velocity| < this = apogee (m/s)
APOGEE_CONFIRM_COUNT = 5      # consecutive readings below threshold
LANDED_VEL_THRESHOLD = 0.5    # near-zero velocity
LANDED_CONFIRM_SECONDS = 5.0  # must be still for this long

# Ground reference
GROUND_SAMPLES = 50           # samples to average for ground-level pressure
