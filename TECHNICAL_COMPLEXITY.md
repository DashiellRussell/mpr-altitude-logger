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

## A Custom Binary File Format — `.bin` Files With Their Own Spec

This project doesn't write CSV. It doesn't write JSON. It invented its own binary file format.

The `.bin` files begin with a proprietary file header:

```
Bytes 0-5:   b'RKTLOG'          ← 6-byte magic number (like PNG has \x89PNG, RKTLOG has... RKTLOG)
Bytes 6-7:   u16 version        ← format version (currently v2, because there was already a v1)
Bytes 8-9:   u16 frame_size     ← 32 bytes per frame
```

A magic number. A version field. A self-describing frame size. This is the same design pattern used by PNG, ELF, and ZIP. For a pressure sensor log file.

After the header, every frame is preceded by a sync marker — `0xAA 0x55` — borrowed from telecommunications protocols. These two bytes exist so that if power cuts mid-write and corrupts a frame, the decoder can byte-scan forward until it finds the next `\xAA\x55` and resync. Corruption recovery. For an SD card in a model rocket.

Each frame is 32 bytes of packed binary data, little-endian (RP2040 native byte order):

```
Offset  Type   Field              Notes
──────  ─────  ─────────────────  ──────────────────────────────────────
0       u32    timestamp_ms       Millisecond-precision flight clock
4       u8     state              7 possible values, gets a whole byte
5       f32    pressure_pa        Raw barometric pressure
9       f32    temperature_c      Sensor temperature
13      f32    alt_raw_m          Unfiltered barometric altitude
17      f32    alt_filtered_m     Kalman-filtered altitude
21      f32    vel_filtered_ms    Kalman-derived velocity (no accelerometer)
25      u16    v_3v3_mv           3.3V rail in millivolts
27      u16    v_5v_mv            5V rail in millivolts
29      u16    v_9v_mv            9V rail in millivolts
31      u8     flags              Bitfield: bit3=error, bits 0-2 reserved
```

Struct format string: `<IBfffffHHHB`. Every type hand-picked for minimum size — `u8` for state (only 7 values need 3 bits, gets 8), `u16` for millivolt readings (range 0–65535, enough for 65V — overkill for a 9V rail), a flags byte with individual bit assignments and 5 reserved bits for future expansion. Of a format that has already been through one version bump.

The flags byte has legacy bits. Bits 0-2 were originally `ARMED`, `DROGUE_FIRED`, and `MAIN_FIRED` — from when the system was going to control pyrotechnic charges. The board has no pyrotechnic charges. The bits are always zero. They're kept for backward compatibility with v1 decoders.

### Three Decoders for One File Format

The custom format requires custom decoders. There are three:

1. **Python decoder** (`tools/decode_log.py`, 237 lines) — reads `.bin` files, outputs CSV, optionally generates 4-subplot matplotlib charts (altitude, velocity, pressure, power rails). Handles both v1 and v2 frame formats. Reports skipped bytes for corruption diagnostics. Prints a full flight summary with state transition timestamps.

2. **TypeScript decoder** (`tools/ground-station/packages/shared/src/decoder.ts`, 150 lines) — reimplements the exact same binary parsing in TypeScript for the web dashboard. Uses `DataView` with little-endian reads. Field-by-field offset table defined in `constants.ts`. Same sync-byte scanning. Same version detection. Same v1/v2 branching.

3. **Python simulation harness decoder** (`tests/sim_harness.py`) — imports and uses `decode_log.py` to verify encode/decode round-trips in the integration tests.

The TypeScript decoder mirrors the Python one so precisely that the constants file (`constants.ts`, 113 lines) duplicates every config value — Kalman defaults, state machine thresholds, voltage rail specs, ADC reference voltage, flag bitmasks — because the ground station needs to understand the same binary format that the Pico writes. Two languages. Same spec. Maintained in parallel.

The constants file even defines state colors for terminal and web display:
```typescript
export const STATE_COLORS: Record<string, string> = {
  PAD: 'white',
  BOOST: 'red',
  COAST: 'yellow',
  APOGEE: 'green',
  DROGUE: 'cyan',
  MAIN: 'blue',
  LANDED: 'magenta',
};
```

Seven colors. For seven states. Of a data logger.

### Each Flight Gets Its Own Folder

Flight data isn't dumped loose onto the SD card. Each flight creates a numbered directory:

```
/sd/flight_001/
    flight.bin         ← the proprietary binary telemetry
    preflight.txt      ← preflight check results and metadata
    boot.txt           ← complete serial boot output captured and saved
```

Auto-incrementing folder numbers prevent data overwrites on reboot. There's even a naming override mechanism — drop a `_flight_name.txt` file on the SD card and the system reads it, uses it as the folder name, and deletes the file (one-shot consumption). If the override name already has a `flight.bin`, it falls through to auto-numbering. Edge case handling for a folder naming feature.

### Zero-Allocation Writes

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

No `struct.pack()` (which allocates a new `bytes` object). `pack_into()` writes directly into the pre-allocated buffer. Zero heap allocation in the hot loop. Because MicroPython's garbage collector is stop-the-world, and a GC pause during a 50 Hz sensor read could cause a missed frame.

At 50 Hz with 34-byte frames (2 sync + 32 data), that's 1,700 bytes/second. An 8GB SD card gives ~55 days of continuous recording. For a flight that lasts 60 seconds.

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

## 102 Tests, a Simulated Pico, and a Fault Injection Framework

The test suite contains **102 test functions** across **2,913 lines of test code** — for firmware that runs on a $4 microcontroller with no way to execute pytest. The RP2040 has no test runner. So the team built a simulated one.

### The Simulated Pico Environment

`conftest.py` (62 lines) constructs a fake MicroPython runtime on CPython. Before any avionics code is imported, it:

- Creates mock `machine` module with fake `Pin`, `SPI`, `SoftI2C`, `ADC`, `freq()`, and `WDT`
- Creates mock `_thread` module with fake `start_new_thread`
- Creates mock `sdcard` module with fake `SDCard`
- Patches `time.ticks_ms`, `time.ticks_us`, `time.ticks_diff`, and `time.sleep_ms` onto CPython's `time` module (MicroPython-only functions that don't exist on desktop Python)
- Carefully preserves stdlib `logging` before the project's `logging/` directory shadows it on `sys.path`

The avionics code doesn't know it's not on a Pico. It imports `machine`, calls `Pin()`, reads "ADC" values — all hitting mocks.

### The Simulation Harness — A Virtual Flight Computer

`sim_harness.py` (435 lines) is the crown jewel. It builds a complete `PicoSim` class that mirrors `main.py`'s Core 0 loop: sensor read → altitude conversion → Kalman filter → state machine → binary logger. Same code path. Same pipeline. No hardware.

The `PicoSim` takes a **sensor provider** — a Python generator yielding `(pressure_pa, temperature_c)` tuples — and runs the full avionics pipeline against it. Every frame produces a result dict with timestamp, state, filtered altitude, filtered velocity, voltages, and flags. The `SimResult` object tracks state transitions, max altitude, max velocity, flight duration, and provides query helpers like `state_at(time_s)` and `reached_state(LANDED)`.

The simulation even supports **optional binary logging** — it writes actual `.bin` files using the real `FlightLogger` class, which are then decoded by the real `decode_log.py` decoder for round-trip verification.

### 10 Fault Injection Primitives

The harness ships with a fault injection framework. Ten composable generators that wrap any sensor provider and corrupt its output in specific ways:

| Fault Injector | What It Does |
|---|---|
| `from_simulate()` | Wraps the physics engine as sensor data (pressure from ISA atmosphere model) |
| `from_pressure_sequence()` | Raw pressure array → sensor generator |
| `constant()` | Steady-state: fixed pressure for N frames (pad/ground testing) |
| `noise_overlay()` | Gaussian noise on pressure and temperature channels (configurable σ) |
| `sensor_dropout()` | `SENSOR_FAULT` sentinel for N frames at frame index X (I2C failure) |
| `pressure_spike()` | Replace pressure with arbitrary value for N frames (glitch injection) |
| `gradual_drift()` | Linear pressure offset increasing over time (barometric weather change) |
| `stuck_sensor()` | Freeze output at the value from frame X for N frames (sensor lock-up) |
| `intermittent_dropout()` | Multiple dropout windows: list of `(start_frame, duration)` tuples |
| `below_ground_landing()` | Gradually increase pressure during descent (landing in a valley below launch) |
| `angled_flight()` | Scale pressure deviation by a fraction (off-axis flight: 70% effective altitude) |
| `temperature_ramp()` | Linear temperature change over time (thermal soak or altitude cooling) |

These compose. You can stack `noise_overlay` on top of `sensor_dropout` on top of `from_simulate`. The integration tests do exactly this.

### 18 Test Classes, 70 Integration Tests

The integration suite (`test_integration.py`, 858 lines) is organized into 18 test classes covering scenarios that would be impossible to test on real hardware without actually launching rockets:

**Normal Flight** (12 tests) — Full state sequence verification across 5 motor classes (D12, E12, F32, G40, H100, I218, J350). Checks that all 7 states occur in order. Validates apogee altitude falls within expected range (100–800m for H100). Confirms positive max velocity, reasonable flight duration (5–300s), zero error frames in clean flights, finite values in every frame, and correct timestamps on every state transition.

**Ideal Flight** (4 tests) — Textbook-perfect conditions. Verifies deterministic output (two identical runs produce identical apogee within 0.01m). Checks monotonically increasing altitude during BOOST (every frame ≥ previous frame − 0.5m tolerance).

**Noisy Flight** (4 tests) — 100 Pa Gaussian noise on pressure. 200 Pa noise on pad (must NOT false-trigger launch). Light noise (20 Pa) through full descent. Combined pressure + temperature noise (80 Pa + 2°C).

**Angled Flight** (3 tests) — 70% effective altitude still detects all states. 50% effective altitude produces measurably lower apogee. 30% effective altitude (severe off-axis) doesn't crash the pipeline.

**False Launch Recovery** (2 tests) — Brief altitude spike then return to ground: must recover to PAD. Walking up stairs (3m altitude gain over 3 seconds): must stay on PAD. The walking-upstairs test generates 75 frames of gradual 0.48 Pa/frame pressure decrease, waits, then returns to ground level.

**Sensor Dropout** (4 tests) — 10 frames of `SENSOR_FAULT` during coast produce exactly 10 error frames with `flags=0x08`. System recovers after dropout and eventually reaches LANDED. Dropout on pad doesn't trigger state changes.

**Multiple Dropouts** (3 tests) — Three separate dropout windows (8, 12, 5 frames) produce exactly 25 error frames total. Flight completes despite multiple fault windows. 25-frame (1 second) sustained dropout during coast — system survives.

**Pressure Spikes** (4 tests) — Single-frame spike to 50,000 Pa during coast doesn't skip APOGEE. Spike on pad doesn't false-trigger launch. 5-frame spike during BOOST — system survives. Impossibly low pressure (100 Pa) — all values remain `math.isfinite()`.

**High Altitude** (3 tests) — J350 motor (highest impulse): all values finite, all states reached, higher apogee than H100.

**Short Flight** (2 tests) — D12 on a 300g rocket: at least detects BOOST. All frames contain finite values.

**Below-Ground Landing** (3 tests) — 50m valley below launch: negative AGL values confirmed, all values finite, states still detected.

**Wind Gust / Barometric Drift** (2 tests) — 5m-equivalent slow drift: doesn't false-trigger. Rapid drift (5 Pa/frame): pipeline survives.

**Stuck Sensor** (3 tests) — 100 frames of frozen readings during coast: no crash, correct frame count. Frozen readings on pad: stays on PAD.

**Temperature Effects** (3 tests) — Rising temp, falling temp, and logging verification (end temp > start temp when ramping up).

**Voltage Brownout** (3 tests) — Injected low voltages appear in frame data. Gradual voltage drain (battery discharge simulation). Default voltages are nominal (3300, 5000, 9000 mV).

**Extended Pad Wait** (2 tests) — 10 minutes (15,000 frames) idle on pad, then H100 flight: still works. 5,000 frames of pad with zero spurious state transitions.

**SD Card Failure** (5 tests) — Binary write creates valid decodable file. Sensor faults produce error frames in binary output. File truncated at 50% (simulating full SD): decoder handles gracefully. Partial frame at end (mid-write power loss): skipped by decoder. `sd_failed` flag makes `write_frame()` a silent no-op (verified: 10 frames before failure, 0 frames after).

**SD Capacity Calculation** (1 test) — Mathematically verifies that 8GB at 34 bytes/frame at 25 Hz = >2,600 hours of recording.

**Marginal Launch** (2 tests) — Heavy rocket on small motor. High drag coefficient (Cd=1.2) produces lower apogee than nominal (Cd=0.45).

**Round-Trip Binary** (3 tests) — Write `.bin` → decode with `decode_log.py` → compare: frame count matches, field values match within f32 precision (0.5 tolerance), state sequences are identical.

### The Test Metadata Schema

`test_meta.py` (1,091 lines) defines structured metadata for every single test. Each test gets:

```python
{
    "desc": "What the test does (one sentence)",
    "criteria": ["List of pass/fail assertions"],
    "config_keys": ["KALMAN_Q_ALT", "LAUNCH_ALT_THRESHOLD", ...],
    "category": "Integration",
    "scenario": "H100 / 2.5kg / nominal",
    "tags": ["smoke", "regression", "fault-injection"],
}
```

This metadata feeds into the diagnostic TUI so that when a test fails, the operator sees exactly which config parameters are relevant, what the pass criteria were, and what scenario was being tested. The tests have their own rich documentation system. The metadata file is longer than the Kalman filter, the state machine, and the barometer driver combined.

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

## Onboard Diagnostics — 1,115 Lines of Self-Testing Firmware

The Pico doesn't just run flight code. It also runs a full diagnostic suite on itself.

### First-Boot Hardware Verification

`hw_check.py` (383 lines) is a standalone diagnostic — zero dependencies on the avionics codebase. You flash this onto a bare Pico and it systematically tests every component:

1. **LED test** — blinks 5 times, visual confirmation
2. **I2C bus scan** — scans the full address space, auto-identifies known devices by address (BMP180 at 0x77, MPU6050 at 0x68, HMC5883L at 0x1E, SSD1306 OLED at 0x3C)
3. **BMP180 barometer** — chip ID verification (0x55), soft reset, 22-byte calibration EEPROM read with sanity check, temperature range validation (-40°C to 85°C), and a **10-sample pressure noise characterization** that warns if peak-to-peak noise exceeds 200 LSB
4. **ADC voltage rails** — all three rails read through their dividers, checked against expected ranges (3.3V: 3.0–3.6V, 5V: 3.0–7.0V, 9V: 5.0–12.0V)
5. **SD card** — SPI init at 400 kHz (SD spec), mount FAT, report total/free space, **write → read → compare → delete** cycle, list existing flight logs
6. **Loop timing** — 100 full BMP180 temp+pressure read cycles benchmarked at microsecond precision. Reports best/avg/worst. Calculates headroom against configured Hz target. Warns if worst-case exceeds frame budget

Prints a scored summary: `6/6 tests passed → ★ ALL CLEAR ★`.

### The Deep Diagnostics Framework

`pico_diag.py` (1,115 lines) goes further. This is a full diagnostic framework that runs *on the Pico itself*, with its own statistics engine. It implements:

**Welford's Online Algorithm** — A streaming statistics class (`StreamStats`) that computes mean, standard deviation, min, and max in a single pass with no array storage. Uses Welford's numerically stable online variance algorithm. Even implements square root via Newton's method because MicroPython doesn't guarantee `math.sqrt()` availability:

```python
def std(self):
    v = self._m2 / self.n
    s = v
    for _ in range(10):  # Newton's method
        s = 0.5 * (s + v / s)
    return s
```

A statistics library. Hand-rolled. On a microcontroller. For diagnostics.

**Built-in Histogram** — Fixed-bin histogram class that renders ASCII bar charts over serial. Used for timing distribution analysis.

**Test 1: Sensor Bench** — 1,000 BMP180 reads with microsecond-precision timing on every read. Reports timing distribution as a histogram. Calculates pressure noise in Pascals, converts to altitude noise (~0.083 m/Pa at sea level). Detects I2C clock stretching by flagging any read >2x the average.

**Test 2: SD Card Bench** — Two phases:
- *Phase 1*: 1,000 frame writes with per-write microsecond timing, per-flush timing, per-sync timing. Histogram of write latency. Warns if any write exceeds the 40ms frame budget.
- *Phase 2*: **5-minute sustained write at 25 Hz**. Real-time reporting every 30 seconds: average latency, max latency, bytes written, error count. Simulates an actual flight-length logging session. Reports total errors at the end.

**Test 3: Loop Budget** — The pipeline profiling test. Runs 1,000 frames through the *real* avionics pipeline (barometer → altitude → Kalman → FSM → power → struct pack) and individually times every stage:

```
Stage              Avg(us)   Max(us)  % Budget
──────────────────────────────────────────────
Baro read            31042     33891    77.6%
Alt calc                89       142     0.2%
Kalman                 156       203     0.4%
FSM                     67       109     0.2%
Power read             412       587     1.0%
Struct pack             34        47     0.1%
──────────────────────────────────────────────
TOTAL                31800     34979    79.5%
Headroom              8200      5021    20.5%
```

Per-stage microsecond profiling of the entire data pipeline. On a $4 chip. With percentage-of-budget calculations.

**Test 4: RAM Profile** — Measures memory consumption of every avionics object: `AltitudeKalman`, `FlightStateMachine`, `FlightLogger`, `BMP180`. Reports bytes consumed by each. Then runs 1,000 frames through the hot loop with `gc.collect()` every 100 frames, tracking memory at each checkpoint. Detects memory leaks by comparing start and end values. Warns if leak exceeds 100 bytes per 1,000 frames.

**Test 5: Float Precision** — 10,000 iterations of the Kalman filter fed constant altitude (500.0m). Reports drift at every 1,000 iterations. Then 10,000 iterations with ramping input (0→10,000m). Checks final altitude tracking error, velocity estimate error against expected (25.0 m/s for 1m/frame at 25Hz), and verifies covariance matrix diagonal remains positive. Pure math stress test — no hardware needed.

**Test 6: Dual-Core Stress** — Two-phase test:
- *Phase 1*: 30 seconds of Core 0 running the full pipeline solo. Records timing statistics.
- *Phase 2*: 30 seconds of Core 0 running the pipeline while Core 1 toggles the LED at 25ms intervals via `_thread`. Compares timing between phases to quantify GIL contention impact.

Then there's `tools/pico_diag_tui.py` (1,616 lines) — a laptop-side terminal UI that drives all these diagnostics over USB serial, with rich formatting, progress bars, and interactive test selection.

---

## Runtime Health Monitoring — Every Frame is Audited

During flight, the system doesn't just log data — it monitors itself:

### Voltage Rail Monitoring at 50 Hz

The `PowerMonitor` class reads three voltage rails through ADC pins, on every single frame, 50 times per second:

- **3.3V rail** — direct ADC read (within ADC range)
- **5V rail** — through a 500Ω/680Ω voltage divider (ratio 1.735)
- **9V rail** — through a 2k/1k voltage divider (ratio 3.0)

Each read does a throwaway `read_u16()` first to handle the ADC mux switching delay (the RP2040 ADC multiplexer needs time to settle after switching channels). Stuck-low readings (raw < 100 counts) are flagged as disconnected pins and return 0mV.

At boot, `check_health()` validates all rails against spec (3.3V: 3.0–3.6V, 5V: 4.5–5.5V, 9V: 8.0–10.0V). In flight, the raw millivolt values are packed into every binary frame — 6 bytes per frame dedicated to voltage telemetry. That's 18% of the data payload spent on power monitoring.

This data isn't used for any in-flight decisions. It exists so that post-flight analysis can correlate power anomalies with sensor glitches: a voltage dip at the same timestamp as a noisy pressure reading would explain the noise.

### Frame-Level Error Flagging

Every frame carries a flags byte. Bit 3 is the error flag. If the sensor read throws an exception, or the SD card write fails, the flag is set in that frame's binary record. The error frame still gets written — with zeroed sensor data — so the decoder knows exactly which frames were affected and when. No silent data gaps.

### SD Card Health Tracking

The logger tracks `_sd_failed` as a persistent flag. On the first write failure, it retries once. If the retry fails, the flag goes true and all subsequent `write_frame()` calls become no-ops — the pipeline keeps running (sensor reads, Kalman, FSM all continue) but stops trying to write to a dead card. The LED switches to solid-on to visually indicate data loss. The error propagates into the flags byte of every frame from that point forward.

### 1 Hz Console Telemetry

Every second, the system prints a status line over USB serial:

```
[COAST  ] alt=  312.4m  vel= +2.1m/s  P=97532Pa  T=21.3°C  3V3=3312mV  50Hz 3847us  #2450
```

Seven fields of runtime telemetry: flight state, filtered altitude, filtered velocity, raw pressure, temperature, 3.3V rail voltage, achieved sample rate (actual Hz, not target), average frame processing time in microseconds, and total frames logged. This is real-time performance instrumentation. Every second. In production.

### Hardware Watchdog

A 5-second hardware WDT runs from boot. If the firmware hangs — I2C bus lock-up, SD card blocking, infinite loop in the filter — the watchdog hard-resets the Pico. The WDT is fed at the top of every frame in the main loop, during every iteration of every preflight retry loop, and before/after every `os.sync()` call (which can block for hundreds of milliseconds on large FAT updates).

### State Transition Auditing

Every state change triggers an immediate `flush()` + `os.sync()`, logging the transition moment to persistent storage within milliseconds. If the rocket crashes and power cuts during descent, the SD card has at minimum: the exact timestamp of every state transition up to the crash.

---

## Binary Format Efficiency — 2,900+ Hours on an 8GB SD Card

The custom binary format was designed for maximum recording density on minimal hardware:

| Metric | Value |
|---|---|
| Frame size (data) | 32 bytes |
| Frame size (wire: sync + data) | 34 bytes |
| Overhead (sync headers) | 5.9% |
| Frames per second (50 Hz) | 50 |
| Bytes per second | 1,700 |
| Bytes per minute | 102,000 |
| Bytes per hour | 6.12 MB |
| **8 GB SD card capacity** | **~1,300 hours at 50 Hz** |
| **8 GB SD card capacity** | **~2,600 hours at 25 Hz** |
| Frames per GB | ~31.6 million |
| Flight time per GB (50 Hz) | ~175 hours |

For context: a typical rocket flight lasts 30–90 seconds. At 50 Hz, a 90-second flight produces 4,500 frames = 153 KB. An 8 GB SD card could store **over 50,000 flights**.

The system could log continuously for **54 days at 50 Hz** before filling the card. You will run out of battery, patience, and reasons to keep logging long before you run out of storage.

Compare this to CSV logging. The same frame data as CSV — with headers, decimal formatting, commas, and newlines — would be roughly 200+ bytes per frame. The binary format is **6x more space-efficient** than CSV while also being faster to write (no string formatting in the hot loop, no float-to-ASCII conversion, no newline handling) and more reliable to parse (fixed-width frames with sync markers vs. variable-length text lines that can be corrupted by partial writes).

The test suite includes a mathematical verification of this:

```python
def test_sd_capacity_calculation(self):
    frame_wire_size = 2 + FRAME_SIZE  # 34 bytes
    bytes_per_second = frame_wire_size * 25
    sd_capacity = 8 * 1024 * 1024 * 1024
    hours = sd_capacity / bytes_per_second / 3600
    assert hours > 2600
```

Even the storage capacity has a test case.

---

## Additional Tools

- **`tools/preflight.py`** (1,078 lines) — interactive TUI with USB serial connection to Pico, 5 hardware checks, live 2 Hz sensor monitoring with sparkline altitude display, voltage bar graphs, GO/NO-GO assessment, manual override, and ground pressure recalibration
- **`tools/postflight.py`** (1,060 lines) — post-flight TUI with binary log download over serial (base64 chunked transfer), ASCII altitude charts with actual-vs-simulated overlay, state timeline with colored segments, velocity sparklines, power rail voltage ranges, and Cd adjustment suggestions
- **`tools/simulator_tui.py`** (1,119 lines) — interactive terminal UI for the flight simulator
- **`tools/launch.py`** (471 lines) — launch operations management
- **`tools/seed_flight.py`** (382 lines) — synthetic flight data generator for testing
- **`tools/decode_log.py`** (237 lines) — binary log decoder with 4-subplot matplotlib charts (altitude raw+filtered, velocity, pressure, power rails) and state transition markers
- **`tools/tui.py`** (806 lines) — general terminal UI utilities

---

## Summary

This project took the spec "read pressure, write to SD card, 10 Hz minimum" and delivered:

- A Bayesian state estimator deriving velocity from pressure alone — with hand-unrolled 2x2 matrix math and covariance clamping
- A 7-state deterministic finite automaton with dual-gate launch detection, false-trigger recovery, consecutive-frame debouncing, timeout fallbacks, and fractional-altitude parachute transitions
- Pipelined sensor reads overlapping ADC conversion with frame processing to hit 5x the required sample rate
- Microsecond-precision spin-wait timing with overflow-safe tick arithmetic
- A custom binary file format with magic numbers, version headers, sync markers, and three parallel decoder implementations in two languages
- Zero-allocation frame logging with pre-allocated buffers to avoid garbage collector pauses
- A three-tier flush strategy with state-change-triggered metadata sync and crash-recovery resync
- A hardware watchdog with 5-second timeout, fed at 15+ points throughout the code
- A 7-step preflight sequence with triple-retry on every hardware init and manual override support
- Runtime self-monitoring: 1 Hz console telemetry, per-frame error flagging, SD card health tracking, and 50 Hz voltage rail logging
- A 102-test suite with a simulated Pico, 10 fault injection primitives, 18 test scenario classes, and 1,091 lines of test metadata
- An on-device diagnostic framework with Welford's algorithm, ASCII histograms, per-stage pipeline profiling, memory leak detection, Kalman float drift analysis, and dual-core interference testing
- A full 1D physics simulator with ISA atmosphere, aerodynamic drag, and 7 built-in motor models
- An 804-line OpenRocket importer handling 50+ unit conversions and three file formats
- A React + TypeScript ground station with 3D rocket visualization, live serial telemetry, and post-flight analysis
- Binary format efficient enough to log **2,600+ hours on an 8 GB SD card** — approximately 50,000 rocket flights
- 26,000 lines of code across 109 files

It reads pressure. It writes to an SD card. It does it *really, really well*.

---

*MPR Altitude Logger — UNSW Rocketry, 2026*
