# CLAUDE.md — MPR Altitude Logger

You are working on MPR Altitude Logger — a dual-core Raspberry Pi Pico (RP2040) avionics flight computer for a university rocketry team (UNSW Rocketry). The codebase is MicroPython targeting the RP2040, with Python 3 tooling for post-flight analysis.

## Project Context

- **Owner**: Dash (Engineering/Science student, UNSW Sydney)
- **Team**: UNSW Rocketry — building avionics boards for competition rockets
- **Hardware**: Raspberry Pi Pico (RP2040, dual Cortex-M0+, 264KB RAM), BMP280 barometer, 8GB SD card (SPI), buck/boost converters for 3.3V/5V/9V rails, piezo buzzer, physical ARM switch, deployment e-match output
- **Runtime**: MicroPython v1.22+ on bare metal RP2040
- **Competition context**: Australian Universities Rocketry Challenge (AURC) 2026

## Architecture

```
Core 0 (time-critical, 25 Hz):
  Sensor read → Kalman filter → State machine → SD card log

Core 1 (slower, ~20 Hz):
  Deployment pulse mgmt → LED patterns → Buzzer → Recovery beacon
```

Flight states: `PAD → BOOST → COAST → APOGEE → DROGUE → MAIN → LANDED`

Shared state between cores uses simple globals (acceptable for MicroPython's cooperative threading model — no mutex needed for atomic-ish reads of single values).

## Repository Structure

```
avionics/
├── config.py                 # Pin assignments, thresholds, tuning constants
├── main.py                   # Entry point — dual-core orchestration
├── hw_check.py               # Standalone first-boot hardware verification
├── ground_test.py            # Pre-flight check (depends on avionics modules)
├── sensors/
│   ├── barometer.py          # BMP280 I2C driver + hypsometric altitude
│   └── power.py              # ADC voltage rail monitoring
├── flight/
│   ├── kalman.py             # 1D Kalman filter (altitude + velocity state)
│   └── state_machine.py      # Flight phase detection + deployment logic
├── logging/
│   ├── datalog.py            # Binary frame logger (28-byte frames, sync headers)
│   └── sdcard_mount.py       # SD card SPI mount/unmount
├── utils/
│   └── hardware.py           # LED, buzzer, deploy channel, ARM switch
└── tools/                    # Laptop-side Python 3 scripts (NOT for Pico)
    ├── decode_log.py         # Binary .bin → CSV + matplotlib plots
    ├── simulate.py           # 1D Euler flight sim with drag/atmosphere
    └── openrocket_import.py  # OpenRocket CSV export → dashboard format
```

There is also a React dashboard artifact (`flight-review-dashboard.jsx`) for interactive post-flight review with actual-vs-simulated overlay.

## Key Technical Details

### Constraints (RP2040 / MicroPython)
- **264 KB RAM** — no large buffers, no pandas, no numpy. Everything is hand-rolled.
- **No hardware FPU** — float math is software-emulated. Keep inner loops lean.
- **MicroPython `_thread`** — only two threads (one per core). No thread pools, no asyncio on Core 1.
- **SPI SD writes block** — `flush()` every ~25 frames (1 sec). Never flush in the hot path every frame.
- **No filesystem journaling** — if power cuts mid-write, the last frame may be corrupt. The sync header (`\xAA\x55`) lets the decoder resync.
- **I2C clock stretch** — BMP280 can hold the bus. The 400kHz freq is fine but don't assume reads are instantaneous.
- **ADC is 12-bit but `read_u16()` returns 16-bit** — MicroPython scales it. Voltage divider ratios in `config.py` must match actual resistors on the board.

### Binary Log Format
Each frame = 2 sync bytes + 28 data bytes:
```
Sync:    \xAA\x55
Frame:   u32 timestamp_ms | u8 state | f32 pressure_pa | f32 temperature_c |
         f32 alt_raw_m | f32 alt_filtered_m | f32 vel_filtered_ms |
         u16 v_batt_mv | u8 flags
```
File header: `RKTLOG` (6B) + u16 version + u16 frame_size = 10 bytes.

### Kalman Filter
Constant-velocity model: state = [altitude, velocity], measurement = barometric altitude only. No accelerometer — the velocity estimate comes purely from the filter's prediction-correction cycle. This is sufficient for apogee detection but not for active guidance.

Process noise (`Q`) and measurement noise (`R`) in `config.py` are the main tuning knobs:
- Lower `KALMAN_Q_*` = smoother but slower to respond to real changes
- Higher `KALMAN_R_ALT` = trust the model more, trust barometer less
- Tune on the ground by logging raw vs filtered while shaking the board

### State Machine Safety
- Deployment ONLY fires if: (a) state machine says to, AND (b) physical ARM switch is closed, AND (c) hasn't already fired
- Deploy pin is active HIGH, held LOW at boot by `Pin(OUT, value=0)`, with a timed auto-shutoff (`DEPLOY_PULSE_MS`)
- `hw_check.py` tests the deploy pin as INPUT with pull-down — never drives it HIGH during testing

### OpenRocket Integration
The importer (`tools/openrocket_import.py`) handles:
- OpenRocket CSV comment-based event annotations (`# Event APOGEE occurred at t=12.345 seconds`)
- Auto-detection of column separators (comma, semicolon, tab)
- Unit detection from headers like `Altitude (ft)` → auto-converts to SI
- Mapping 50+ possible OpenRocket column names to our internal fields
- RASP `.eng` thrust curve file parsing

## Operational Workflow

### Phase 1: Board Bringup
1. Flash MicroPython onto Pico
2. Copy `hw_check.py` as `main.py` → reboot → check serial output
3. Fix any FAIL results before proceeding

### Phase 2: Flight Computer Load
1. Copy entire `avionics/` folder to Pico root
2. Copy `main.py` to Pico root
3. Ensure `sdcard.py` driver is on the Pico filesystem
4. Ground test: `import ground_test; ground_test.run()`

### Phase 3: Pre-Flight Simulation
```bash
# From OpenRocket
python tools/openrocket_import.py sim_export.csv -o sim_predicted.csv

# Or standalone
python tools/simulate.py --mass 2.5 --motor Cesaroni_H100 --cd 0.45 --diameter 0.054
```

### Phase 4: Post-Flight Analysis
```bash
python tools/decode_log.py flight.bin --plot
# Then load flight.csv + sim_predicted.csv into the React dashboard
```

## Coding Conventions

- **MicroPython on-device code**: no type hints (saves RAM), minimal imports, avoid creating objects in hot loops, prefer pre-allocated buffers. Use `time.ticks_ms()` / `time.ticks_diff()` for all timing (handles overflow).
- **Laptop-side tools** (`tools/`): standard Python 3, type hints welcome, can use matplotlib/numpy but keep them optional imports with helpful error messages.
- **Config changes**: all tunable values go in `config.py`, never hardcode thresholds in logic files.
- **Comments**: docstrings on all classes/functions. Inline comments for non-obvious hardware interactions or RP2040 gotchas.
- **Safety-critical code**: any change to `state_machine.py`, deployment logic in `hardware.py`, or the ARM switch handling must be explicitly flagged and reviewed. Never bypass the ARM check.

## Common Tasks You May Be Asked To Do

### Add a new sensor (e.g., IMU, GPS)
1. Create `sensors/new_sensor.py` with a driver class
2. Add pin assignments to `config.py`
3. Add fields to the binary log frame in `logging/datalog.py` (update `FRAME_FORMAT` and `FRAME_SIZE`)
4. Update `decode_log.py` to parse the new fields
5. Add the sensor read call to Core 0's main loop in `main.py`
6. Update `hw_check.py` to test the new hardware
7. Update the dashboard to display the new data

### Tune the Kalman filter
- Edit `KALMAN_Q_ALT`, `KALMAN_Q_VEL`, `KALMAN_R_ALT` in `config.py`
- Lower Q = smoother, Higher R = trust model more
- Test by running `ground_test.py` and comparing raw vs filtered output

### Change pin assignments
- Edit ONLY `config.py` — all other files import from there
- Re-run `hw_check.py` to verify the new wiring

### Add a new flight state or deployment event
- Add state constant and name to `flight/state_machine.py`
- Add transition logic in the `update()` method
- Add LED pattern in `utils/hardware.py` `LED_PATTERNS` dict
- Update `STATE_NAMES` in `decode_log.py` and `openrocket_import.py`

### Change sample rate
- Edit `SAMPLE_RATE_HZ` in `config.py`
- Also adjust `LOG_FLUSH_EVERY` to maintain ~1 second flush intervals
- Run timing test in `hw_check.py` to verify headroom at new rate

### Modify the log format
- Update `FRAME_FORMAT` string and `FRAME_SIZE` in `logging/datalog.py`
- Update `write_frame()` parameters
- Update `decode_log.py` with matching struct format
- Increment the version number in the file header
- Update `FRAME_FORMAT` / `FIELD_NAMES` in the decoder

## What NOT To Do

- **Never put the full flight computer on the Pico without running `hw_check.py` first**
- **Never test deploy pin by driving it HIGH with an e-match connected**
- **Never use `time.sleep()` in Core 0's sensor loop** — it breaks the timing. Use spin-wait with `ticks_diff`.
- **Never allocate memory in the hot loop** — no string formatting, no list appends, no dict creation per frame
- **Never `import` inside a loop** — MicroPython import is slow
- **Never assume the SD card write succeeded** — check for exceptions, the card can fail mid-flight from vibration
- **Never bypass the ARM switch check** for deployment, even in testing
- **Don't use asyncio** — it adds overhead and complexity for no benefit in this two-core architecture
- **Don't add network/WiFi code** to the flight firmware — it's a distraction and potential failure mode in flight

## Testing Approach

- `hw_check.py` — standalone hardware verification, no dependencies
- `ground_test.py` — full system integration test, requires all avionics modules
- For Kalman/state machine logic: test on laptop by feeding synthetic pressure sequences through the Python classes (they're pure math, no hardware dependencies)
- `tools/simulate.py` — validate flight predictions before launch
- Post-flight: compare actual vs simulated in the dashboard to tune Cd and validate the model

## Style Preferences

Dash prefers concise, practical output. No boilerplate. Direct communication. Code should be clean but not over-engineered — this is embedded firmware for a student rocketry competition, not enterprise software. Prefer readable over clever. Comment the *why*, not the *what*.
