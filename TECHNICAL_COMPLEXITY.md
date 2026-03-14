# The Most Overengineered Pressure Sensor in the Southern Hemisphere

### A technical breakdown of the MPR Altitude Logger — 26,000 lines of code for a Raspberry Pi Pico, a barometer, and an SD card.

---

## What This Project Actually Is

Three components. A $4 Raspberry Pi Pico (RP2040 — dual Cortex-M0+, 264KB RAM, no FPU). A $2 BMP180 barometric pressure sensor. A $5 SD card on SPI. Total BOM under $15.

The original spec: *"Log pressure at 10 Hz to an SD card."*

What it became: a flight computer running a Kalman filter, a 7-state finite state machine, pipelined sensor reads, microsecond-precision timing loops, pre-allocated zero-GC binary frame logging, a hardware watchdog, triple-redundant boot sequences, an ISA atmosphere physics simulator, an 800-line OpenRocket data import pipeline, a full diagnostic TUI, a React + TypeScript ground station with real-time telemetry, a CI-grade integration test suite with 102 test functions across a hardware-simulated Pico environment, and enough safety checks to satisfy an actual aerospace qualification board.

**109 source files. 26,000 lines of code. For a pressure sensor and an SD card.**

---

## The Numbers

| Metric | Value |
|---|---|
| Total source files | 109 |
| Total lines of code | ~26,000 |
| Python (on-device + tools) | ~15,500 lines |
| TypeScript/React (ground station) | ~10,450 lines |
| Test functions | 102 |
| Test lines | 2,913 |
| Configurable parameters | 30+ |
| Flight states tracked | 7 |
| Binary frame size | 34 bytes (2 sync + 32 data) |
| Sample rate | 50 Hz (5x the original 10 Hz spec) |
| Sensor read pipeline depth | 2 frames |
| LED blink patterns | 7 distinct patterns |
| Motor models in simulator | 7 built-in |
| Safety/sanity checks at boot | 17+ |
| Hardware watchdog timeout | 5 seconds |
| Lines of code per hardware component | ~8,600 |

---

## The Kalman Filter — Because Raw Barometer Data is for Amateurs

The core of the system is a constant-velocity Kalman filter (`flight/kalman.py`), implementing the full predict-update cycle of the linear Kalman equations — on a microcontroller with no floating point unit.

**State vector**: `[altitude, velocity]` — two states estimated from a single measurement. The velocity estimate is entirely *inferred* by the filter's prediction-correction cycle. There is no accelerometer. There is no GPS. The system derives velocity from the rate of change of barometric altitude, smoothed through Bayesian state estimation.

The implementation:

```
PREDICT:
  x_pred = F @ x          (state transition — constant velocity model)
  P_pred = F @ P @ F^T + Q    (covariance propagation)

UPDATE:
  innovation = z - H @ x_pred     (measurement residual)
  S = H @ P_pred @ H^T + R        (innovation covariance)
  K = P_pred @ H^T / S            (Kalman gain)
  x = x_pred + K * innovation     (state update)
  P = (I - K @ H) @ P_pred        (covariance update)
```

The 2x2 covariance matrix is hand-unrolled into four scalar variables (`p00`, `p01`, `p10`, `p11`) because the RP2040 has no matrix library, no numpy, and 264KB of RAM to share with everything else. Every multiply, every add — explicit, manual, no abstraction.

It even guards against numerical instability with covariance clamping:
```python
if self.p00 < 0.0:
    self.p00 = 0.0
if self.p11 < 0.0:
    self.p11 = 0.0
```

And a near-zero divisor guard on the innovation covariance:
```python
if abs(s) < 1e-10:
    return pred_alt, pred_vel
```

Three tuning knobs exposed in config: `KALMAN_Q_ALT` (altitude process noise), `KALMAN_Q_VEL` (velocity process noise), `KALMAN_R_ALT` (barometric measurement noise). Because of course the noise parameters are configurable.

---

## The 7-State Finite State Machine — For a Board With No Deployment Hardware

The state machine (`flight/state_machine.py`) tracks seven flight phases:

```
PAD → BOOST → COAST → APOGEE → DROGUE → MAIN → LANDED
```

This board has no pyrotechnic charges. No servo actuators. No parachute deployment mechanism. No arm switch. Every single state transition exists purely for *logging purposes*. The state machine's output goes to an SD card and an LED.

But the detection logic is comprehensive:

**Launch detection (PAD → BOOST)**: Dual-gate — altitude gain > 15m AND velocity > 10 m/s, both sustained for 500ms continuously. This prevents false triggers from wind gusts, walking up stairs, picking up the board, or driving over speed bumps. A half-second sustained-threshold confirmation window. For a data logger.

**False launch recovery**: If the system enters BOOST but velocity drops below 3 m/s within 2 seconds (configurable `BOOST_RECOVERY_WINDOW`), it resets back to PAD and clears all maxima. This handles the scenario where, for example, someone jiggles the board during pre-launch and the vibration momentarily satisfies both thresholds. The recovery requires 3 consecutive frames below threshold (`BOOST_RECOVERY_COUNT`) to prevent single-frame noise from triggering a false reset of a real launch.

**Burnout detection (BOOST → COAST)**: Triggered when velocity drops more than 5 m/s below the peak recorded velocity. Not an absolute threshold — relative to the maximum. This works across motor classes from Estes D12s to Cesaroni I218s.

**Apogee detection**: Velocity below 2 m/s for 5 consecutive readings. Not one reading — five. With a 30-second timeout fallback (`COAST_TIMEOUT`) in case the Kalman filter is too noisy to converge.

**Apogee dwell**: The system lingers in APOGEE state for 5 frames before transitioning to DROGUE. A deliberate pause in a state machine that controls nothing.

**Main chute transition (DROGUE → MAIN)**: Triggered at 25% of max AGL. Not a fixed altitude — a *fraction of the peak*. This scales automatically whether the rocket hits 200m or 2000m. Plus a landing detection safety net from DROGUE state in case the main never deploys (which is doesn't, because there's no deployment hardware).

**Landing detection**: Velocity below 0.5 m/s sustained for 5 full seconds. Then continues logging for 30 seconds after apogee before cleanly closing the file.

14 configurable threshold parameters govern these transitions. For a data logger.

---

## Pipelined Sensor Reads — Because 50 Hz Wasn't Going to Happen Otherwise

The original spec was 10 Hz. The system runs at 50 Hz. On a microcontroller where the barometer takes 13.5ms to complete a pressure conversion at OSS=2, and the frame budget at 50 Hz is 20ms.

The solution: pipelined ADC reads. The BMP180 driver (`sensors/barometer.py`) exposes a `start()`/`collect()` API that overlaps the ADC conversion with the spin-wait between frames:

```
Frame N:
  collect()     ~1ms   ← read result of conversion started in Frame N-1
  compensate()  ~1ms   ← 11-coefficient BMP180 compensation algorithm
  Kalman        ~0.5ms
  FSM           ~0.2ms
  SD write      ~1ms
  start()       ~0.1ms ← kick off conversion for Frame N+1
  spin-wait     ~16ms  ← conversion runs here, "for free"
```

The conversion happens *during the dead time*. The actual frame work takes ~4ms out of a 20ms budget. The sensor's ADC runs in parallel with the microcontroller's idle loop. This is the kind of optimization you see in real-time avionics systems, not hobby data loggers.

Temperature is only re-read every second (every 50 frames) because it drifts slowly. The blocking 5ms temp read "easily fits in the budget" — a comment that implies someone actually measured the per-operation timing to confirm.

The BMP180 driver itself is a hand-rolled I2C implementation. 11 factory calibration coefficients read from EEPROM on init (`AC1` through `MD`). Big-endian struct unpacking from raw register reads. The full Bosch compensation algorithm with its nested integer arithmetic:

```python
B6 = B5 - 4000
X1 = (self.B2 * (B6 * B6 // 4096)) // 2048
X2 = self.AC2 * B6 // 2048
X3 = X1 + X2
B3 = (((self.AC1 * 4 + X3) << self.oss) + 2) // 4
```

All integer math until the final float conversion. Because there's no FPU, and every unnecessary float operation costs cycles in the hot loop.

---

## Microsecond Timing — Spin-Wait Rate Limiting on a $4 Chip

The main loop uses `time.ticks_us()` for frame timing — microsecond resolution. Not millisecond. Microsecond. On a MicroPython interpreter running on a Cortex-M0+.

```python
interval_us = 1_000_000 // config.SAMPLE_RATE_HZ  # 20,000 µs at 50 Hz
last_time = time.ticks_us()

while True:
    now_us = time.ticks_us()
    dt_us = time.ticks_diff(now_us, last_time)
    if dt_us < interval_us:
        continue   # spin-wait
```

No `time.sleep()`. A hard spin-wait. Because `sleep()` doesn't guarantee wake-up precision, and this system needs consistent sample intervals for the Kalman filter's `dt` parameter to mean something. The filter's state transition matrix is `F = [[1, dt], [0, 1]]` — if `dt` jitters, the velocity estimate degrades.

The system also uses `time.ticks_diff()` everywhere instead of raw subtraction — because `ticks_ms()` wraps around at 2^30 on MicroPython, and `ticks_diff()` handles the modular arithmetic correctly. Overflow-safe timing throughout. For a flight that lasts maybe 60 seconds.

Frame timing is measured and reported at 1 Hz via serial:
```
[COAST  ] alt=  312.4m  vel= +2.1m/s  P=97532Pa  T=21.3°C  3V3=3312mV  50Hz 3847us  #2450
```

That `3847us` is the average frame processing time. The system instruments its own performance. In production. On every flight.

---

## Zero-Allocation Binary Logging — Pre-Allocated Buffers in a MicroPython Hot Loop

The flight logger (`logging/datalog.py`) writes 34-byte binary frames: 2 sync bytes + 32 data bytes. At 50 Hz, that's 1,700 bytes/second. An 8GB SD card gives ~55 days of continuous recording.

The write buffer is pre-allocated at init:
```python
self._write_buf = bytearray(2 + FRAME_SIZE)
self._write_buf[0] = 0xAA
self._write_buf[1] = 0x55
```

And packed in-place every frame:
```python
struct.pack_into(FRAME_FORMAT, self._write_buf, 2, ...)
self._file.write(self._write_buf)
```

No `struct.pack()` (which allocates a new bytes object). `pack_into()` writes directly into the pre-allocated buffer. Zero heap allocation in the hot loop. Because MicroPython's garbage collector is stop-the-world, and a GC pause during a 50 Hz sensor read could cause a missed frame.

The binary format:
```
Sync:    \xAA\x55
Frame:   u32 timestamp_ms | u8 state | f32 pressure_pa | f32 temperature_c |
         f32 alt_raw_m | f32 alt_filtered_m | f32 vel_filtered_ms |
         u16 v_3v3_mv | u16 v_5v_mv | u16 v_9v_mv | u8 flags
```

Little-endian. Struct format string: `<IBfffffHHHB`. Every field type chosen for minimum size — `u8` for state (only 7 values), `u16` for millivolt readings, a flags byte with individual bits.

The file starts with a 10-byte header: `RKTLOG` magic (6B) + `u16` version + `u16` frame size. Versioned binary format. For future backward compatibility. Of a student rocketry data logger.

---

## Tiered Flush Strategy — Because SD Cards Can't Be Trusted at Mach 0.8

SD card writes don't go straight to disk. The FAT filesystem buffers. The SPI interface buffers. The system implements a three-tier flush strategy:

1. **Frame buffering**: `flush()` every 50 frames (~1 second at 50 Hz)
2. **Metadata sync**: `os.sync()` every 3 flushes (~3 seconds) to force FAT metadata to disk
3. **State-change sync**: Immediate `flush()` + `os.sync()` on every state transition

State transitions get immediate sync because they're the most valuable data points. If the rocket crashes and power cuts mid-descent, you want at minimum the timestamp of apogee on the card.

Write failures get one retry before setting `_sd_failed`:
```python
except OSError as e:
    try:
        self._file.write(self._write_buf)
    except OSError:
        self._sd_failed = True
```

Once failed, the sensor loop continues without logging — the system degrades gracefully rather than crashing. The error flag propagates into the binary frame data (bit 3 of the flags byte), and the LED goes solid-on to indicate data loss.

The sync header (`\xAA\x55`) exists specifically for crash recovery. If power cuts mid-write and corrupts a frame, the decoder can scan forward for the next `\xAA\x55` and resync. No journaling needed.

---

## The Preflight Sequence — 7 Steps to Log Some Pressure Data

Boot sequence (`main.py`):

1. **Config validation** — checks every tunable parameter, detects pin conflicts between I2C and SPI assignments
2. **Overclock to 200 MHz** — because the default 125 MHz doesn't leave enough headroom for 50 Hz pipelined reads on software-emulated floating point
3. **LED init** — hardware Timer callback at 25ms tick rate, running as a soft IRQ. Not `_thread`. Not asyncio. A Timer peripheral callback that fires between MicroPython bytecodes to avoid cross-core GIL contention
4. **Hardware watchdog** — 5-second WDT, fed throughout boot and the main loop. If the firmware hangs, the board hard-resets
5. **SD card mount** — 3 attempts with unmount/remount between retries. Clean up stale mounts from interrupted previous boots. Init at 400 kHz (SD spec), then ramp to 10 MHz for data transfer. Sends 80 dummy clocks with CS high for the SD card's native init sequence
6. **Barometer init** — 3 attempts. Chip ID verification (0x55). Soft reset. Calibration EEPROM read with sanity check (rejects all-zero or all-0xFF). 10-sample pressure noise characterization
7. **Power rail check** — reads 3.3V (direct), 5V (through 500Ω/680Ω divider), 9V (through 2k/1k divider). Flags out-of-spec voltages. Throwaway ADC read to handle mux switching delay
8. **Ground pressure calibration** — averages 50 barometric samples with WDT feeding between reads
9. **Logger open** — creates per-flight directory (`/sd/flight_001/`), writes file header, syncs
10. **Preflight metadata save** — writes preflight results, firmware version, MicroPython version, boot time, voltages to `preflight.txt`
11. **Boot log capture** — saves entire serial boot output to `boot.txt` in the flight folder

If the SD card fails: hard halt. Infinite loop with WDT feed and solid LED. The system refuses to enter flight mode without a working data sink.

If the barometer fails: hard halt. Same thing. No sensor, no point.

If voltage rails are out of spec: warning, but continue. Degraded-but-running beats grounded.

There's even a manual override mechanism — drop a `_manual_override` file on the filesystem and the system skips the 10-second error countdown. One-shot flag, consumed after reading.

---

## The LED System — 7 Blink Patterns With Sub-Millisecond Timing

A single onboard LED communicates system state through blink patterns:

| State | Pattern | Description |
|---|---|---|
| Booting | 250ms on/off | Fast blink |
| Error | Solid ON | Something failed |
| PAD | 1000ms on/off | Slow blink — ready |
| BOOST | 50ms on/off | Rapid flash |
| COAST | 100ms on/off | Medium blink |
| APOGEE | 100/100/100/500ms | Double flash |
| DROGUE | 200ms on/off | Medium-fast |
| MAIN | 300ms on/off | Medium |
| LANDED | 100/100/100/100/100/800ms | Triple flash |

The TimerLED class uses a hardware Timer peripheral in periodic mode at 25ms ticks. The callback runs as a soft IRQ — it fires between MicroPython bytecodes on Core 0, with zero GIL contention. The callback is explicitly documented as "must not allocate" because IRQ handlers in MicroPython that trigger garbage collection will crash.

There's also a `StatusLED` class with a manual `tick()` method for testing contexts that need explicit timing control. Two LED driver implementations. For one LED.

---

## The Test Suite — 102 Tests for Code That Runs on a Chip With No Test Runner

The test suite (`tests/`) contains 102 test functions across 2,913 lines, testing a system that ultimately runs on a $4 microcontroller with no way to execute pytest.

The `sim_harness.py` (435 lines) builds a complete simulated Pico environment. It mocks `machine`, `time.ticks_ms`, the I2C bus, and the SD card filesystem to run the full `sensor → Kalman → FSM → logger` pipeline on a laptop:

- `from_simulate()` — wraps the physics simulator output as synthetic sensor data
- `from_pressure_sequence()` — feed arbitrary pressure arrays
- `noise_overlay()` — adds Gaussian noise to clean profiles
- `sensor_dropout()` — simulates intermittent I2C failures
- `pressure_spike()` — injects glitches to test filter robustness
- `gradual_drift()` — slow barometric drift during flight
- `stuck_sensor()` — sensor returns same value forever
- `intermittent_dropout()` — random frame drops
- `temperature_ramp()` — thermal effects on readings
- `below_ground_landing()` — landing site lower than launch site

The integration tests cover:

- 7 different motor profiles (D12 through J350)
- False launch detection and recovery
- Sensor faults during every flight phase
- Pressure spikes during apogee detection
- Multiple sequential flights without reboot
- Barometric drift compensation
- Angled flight trajectories
- Below-ground landing scenarios
- Binary log encode/decode round-trip verification

Each test has structured metadata (`test_meta.py` — 1,091 lines) with descriptions, pass criteria, related config keys, category badges, and scenario details. Because the tests have their own metadata schema.

---

## The Simulator — A 1D Euler Physics Engine

`tools/simulate.py` (438 lines) implements a 1D flight physics simulator with:

- **ISA atmosphere model** — density and pressure as functions of altitude, troposphere-limited to 11km
- **Euler integration** at 1ms timestep
- **Thrust curve interpolation** — linear interpolation of time/thrust pairs, with a 7-motor built-in database
- **RASP `.eng` file parser** — reads standard motor thrust curve files
- **Aerodynamic drag** — `0.5 * ρ * v * |v| * Cd * A` with altitude-dependent density
- **Mass regression** — linear propellant burn reducing total mass during boost
- **Launch rail constraint** — no lateral motion until altitude exceeds rail length, no negative force on rail
- **Mach number calculation** — from temperature-dependent speed of sound
- **Dual recovery simulation** — drogue and main chute deployment with separate Cd and diameter
- **Ground clamping** — altitude can't go negative

Output sampled at 25 Hz to match the flight logger. Results include time, altitude, velocity, acceleration, Mach number, thrust, drag, mass, pressure, air density, and flight state per frame.

---

## The OpenRocket Importer — 804 Lines to Read a CSV

`tools/openrocket_import.py` handles importing simulation data from OpenRocket, the standard open-source rocket simulator. It's 804 lines because OpenRocket's export format is... creative:

- Auto-detects column separators (comma, semicolon, tab)
- Parses comment-based event annotations (`# Event APOGEE occurred at t=12.345 seconds`)
- Detects units from column headers (`Altitude (ft)` → auto-converts to SI)
- Maps 50+ possible OpenRocket column name variants to internal fields
- Handles RASP `.eng` thrust curve files
- Handles OpenRocket's habit of putting metadata in CSV comments

---

## The Ground Station — A Full React + TypeScript Telemetry Dashboard

Because the data logger needed its own ground station. The `tools/ground-station/` directory contains a TypeScript monorepo with:

- **Shared library** (`packages/shared/`) — binary frame decoder, analysis engine, OpenRocket parser, report generator, constants, type definitions, utility functions. With unit tests.
- **Web app** (`apps/web/`) — React + Vite dashboard with:
  - File upload for binary `.bin` flight logs
  - Altitude, velocity, pressure, and power charts with `ChartTabs` navigation
  - Flight state timeline visualization
  - Flight insights and statistics panels
  - Simulated vs actual overlay comparison
  - A 3D animated rocket model with exhaust flame and parachute components (`RocketScene.tsx`, `RocketModel.tsx`, `ExhaustFlame.tsx`, `Parachute.tsx`)
  - Playback bar with scrubbing
  - CSV/JSON export
  - Flight state badges with color coding
- **Terminal TUI** (`apps/tui/`) — Ink-based terminal interface with:
  - Serial connection to Pico via USB
  - Live telemetry readout with sparkline charts
  - ASCII altitude chart
  - LED indicator mirroring
  - Flight summary panel
  - SD card file browser and download
  - Go/no-go preflight checklist
  - State timeline
  - Sim comparison overlay
  - Voltage bar indicators
  - Post-flight analysis screen
  - Preflight screen (1,117 lines alone)

10,450 lines of TypeScript/React. For a pressure sensor data logger.

---

## The Diagnostic System — 8 Hardware Tests + a Full TUI

`hw_check.py` (383 lines) runs 6 hardware verification tests independently of the flight firmware:

1. **LED test** — 5 blinks, visual confirmation
2. **I2C bus scan** — finds all devices, auto-identifies BMP180, MPU6050, HMC5883L, SSD1306 by address
3. **BMP180 barometer** — chip ID check, calibration EEPROM validation, temp range sanity, 10-sample pressure noise characterization
4. **ADC voltage rails** — all three rails with expected-range validation
5. **SD card** — mount, free space check, write/read/verify cycle, cleanup
6. **Loop timing** — 100 read cycles benchmarked, best/avg/worst reported, headroom calculation against target Hz

Then there's `pico_diag.py` (1,115 lines) — a full on-device diagnostic framework with additional tests for RAM usage, dual-core operation, float performance, error recovery, and endurance testing.

And `tools/pico_diag_tui.py` (1,616 lines) — a terminal UI for running diagnostics interactively over serial.

---

## Additional Tools

- **`tools/preflight.py`** (1,078 lines) — comprehensive pre-flight checklist tool
- **`tools/postflight.py`** (1,060 lines) — post-flight analysis and reporting
- **`tools/simulator_tui.py`** (1,119 lines) — interactive terminal UI for the flight simulator
- **`tools/launch.py`** (471 lines) — launch operations management
- **`tools/seed_flight.py`** (382 lines) — generate synthetic flight data for testing
- **`tools/decode_log.py`** (237 lines) — binary log decoder with optional matplotlib plots
- **`tools/tui.py`** (806 lines) — general terminal UI utilities

---

## The Voltage Monitoring — Watching Three Power Rails at 50 Hz

The `PowerMonitor` class reads three voltage rails through ADC pins, 50 times per second, logged in every single binary frame:

- **3.3V rail** — direct ADC read (within ADC range)
- **5V rail** — through a 500Ω/680Ω voltage divider (ratio 1.735)
- **9V rail** — through a 2k/1k voltage divider (ratio 3.0)

Each read does a throwaway `read_u16()` first to handle the ADC mux switching delay. Stuck-low readings (< 100 raw counts) are flagged as disconnected pins. Boot-time health check validates all rails against expected ranges.

The voltage data isn't used for anything in-flight. It's logged. In case someone wants to correlate a power anomaly with a sensor glitch post-flight.

---

## Summary

This project took the spec "read pressure, write to SD card, 10 Hz minimum" and delivered:

- A Bayesian state estimator deriving velocity from pressure alone
- A 7-state deterministic finite automaton with hysteresis, debouncing, timeout fallbacks, and false-trigger recovery
- Pipelined sensor reads overlapping ADC conversion with frame processing
- Microsecond-precision spin-wait timing at 5x the required sample rate
- Zero-allocation binary logging with sync headers for crash recovery
- A three-tier flush strategy with state-change-triggered metadata sync
- A hardware watchdog with 5-second timeout
- A 7-step preflight sequence with triple-retry on every hardware init
- A 102-test suite simulating sensor faults, pressure spikes, and edge-case flight profiles
- A full 1D physics simulator with atmosphere modeling
- A React dashboard with 3D rocket visualization
- A TypeScript ground station TUI with live serial telemetry
- 26,000 lines of code across 109 files

It reads pressure. It writes to an SD card. It does it *really, really well*.

---

*MPR Altitude Logger — UNSW Rocketry, 2026*
