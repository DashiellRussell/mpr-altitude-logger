# Pico Diagnostic TUI — Implementation Plan

On-device diagnostic tool that runs on the actual RP2040 over USB serial.
Tests everything the laptop simulator **can't**: real I2C timing, SD write
latency, MicroPython float precision, RAM pressure, dual-core interference,
and error recovery under real hardware conditions.

## Why this exists

The laptop test suite (102 tests via `pnpm simulator`) verifies the **logic**
— Kalman filter math, state machine transitions, binary log format. But it
runs on CPython with 64-bit floats, mocked hardware, and unlimited RAM. This
diagnostic runs the **same pipeline on the actual Pico** and answers:

- How much timing headroom do we really have at 25 Hz?
- Does the Kalman filter drift with 32-bit floats over thousands of frames?
- How fast/slow are SD card writes, and do they degrade over time?
- Does Core 1 (LED) interfere with Core 0 (sensor loop) timing?
- What happens when I2C or SD fails — does the system actually recover?

---

## File

**`pico_diag.py`** — single file, lives at repo root next to `hw_check.py`
and `ground_test.py`. Copy to Pico, run via REPL: `import pico_diag`

### Constraints

- MicroPython on RP2040, 264KB RAM total (~45-50KB free after boot)
- No `rich`, no `pytest`, no external deps — raw `print()` with ANSI codes
- Must work over USB serial (Thonny, PuTTY, `screen /dev/tty.usbmodem*`)
- Each test streams output as it runs — no large result buffers
- `input()` for menu (blocks on serial, which is fine)
- Ctrl+C returns to menu (wrap each test in `try/except KeyboardInterrupt`)

### ANSI helpers

Minimal set that works in all serial terminals:

```
CLEAR = '\x1b[2J\x1b[H'   # clear screen + cursor home
BOLD  = '\x1b[1m'
RESET = '\x1b[0m'
RED   = '\x1b[31m'
GREEN = '\x1b[32m'
YELLOW = '\x1b[33m'
CYAN  = '\x1b[36m'
```

No cursor movement, no alternate screen buffer. Just colored text.

### StreamStats (Welford's online algorithm)

Every test uses this instead of storing sample arrays. O(1) memory per metric.

```python
class StreamStats:
    __slots__ = ('n', 'mean', '_m2', 'lo', 'hi')

    def add(self, x): ...     # Welford's update + min/max
    def std(self): ...        # sqrt(m2 / n)
    def report(self, label, unit=''): ...  # formatted print
```

Cost: 5 floats + 1 int = ~28 bytes per instance.

### Histogram (fixed bins)

For timing distributions. Pre-allocated bin counters, no stored samples.

```python
class Histogram:
    def __init__(self, edges):  # e.g. (0, 5, 10, 20, 50, 100, 500)
        self.bins = [0] * len(edges)  # last bin = overflow

    def add(self, x): ...        # linear scan, increment bin
    def print_chart(self): ...   # horizontal '#' bar chart
```

Cost: ~100 bytes per instance.

---

## Test Modules

### 1. Sensor Bench

**What**: BMP280 read timing + noise floor over 1000 reads.

**Why the laptop can't test this**: Mocked `baro.read()` returns instantly.
Real BMP280 I2C read takes ~31ms with clock stretching. Need to know the
actual distribution — are there outliers? How much pressure noise?

**Measures**:
- Per-read time (us): min, avg, max, std via StreamStats
- Timing histogram with edges at (25, 28, 30, 32, 34, 36, 38, 40, 50, 100 ms)
- Pressure noise: std of 1000 pressure readings (converts to altitude noise)
- Temperature stability: std over same window
- Clock stretch detection: flag any reads > 2× average

**Output**:
```
BMP280 Read Timing (1000 samples):
  Min: 31245 us   Avg: 31580 us   Max: 34120 us   Std: 312 us

Pressure Noise:
  Std: 12.3 Pa  (~0.10 m altitude noise)
  Range: 101280-101350 Pa (70 Pa p2p)

Timing Distribution (ms):
  30-32 |########################  (842)
  32-34 |######                    (145)
  34-36 |#                          (12)
  36+   |                            (1) ← clock stretch
```

**Progress**: print dot every 100 reads.

---

### 2. SD Card Bench

**What**: Write/flush/sync latency + sustained 5-minute write test.

**Why**: `FlightLogger` writes 34-byte frames at 25 Hz. The laptop mocks
`file.write()` as instant. Real SD cards over SPI have variable latency —
need to know if worst-case writes exceed the 40ms frame budget.

**Measures**:
- Phase 1 (quick, 1000 frames):
  - `file.write(buf)` latency per frame (StreamStats + Histogram)
  - `file.flush()` latency every 25 frames
  - `os.sync()` latency (if available)
- Phase 2 (sustained, 5 minutes at 25 Hz = 7500 frames):
  - Stats reset every 30 seconds — detect degradation over time
  - Print status line per interval: avg/max write time, total bytes, errors
  - Compare first-minute vs last-minute avg/max

**Uses**: Pre-allocated 34-byte `bytearray` matching `FlightLogger._write_buf`.
Writes to `/sd/_diag_bench.tmp`, deletes after test.

**Output**:
```
SD Write Latency (1000 frames):
  Write: min=45us avg=82us max=1240us
  Flush: min=890us avg=1100us max=3200us
  Sync:  avg=2100us

Sustained (5 min):
  0:30  avg=85us  max=1100us  bytes=25500  err=0
  1:00  avg=83us  max=980us   bytes=51000  err=0
  ...
  5:00  avg=88us  max=1350us  bytes=255000 err=0
  Degradation: +3us avg, +250us max [OK]
```

---

### 3. Loop Budget

**What**: Full pipeline timing breakdown per frame for 1000 iterations.

**Why**: Need to know which stage is the bottleneck and how much headroom
remains within the 40,000 us budget (25 Hz). The laptop runs everything
100x faster so timing is meaningless there.

**Measures** (6 StreamStats, timed with `ticks_us`):
- `t_baro`: `baro.read()` — expected ~31ms (dominates)
- `t_alt`: `pressure_to_altitude()` — expected <100us
- `t_kalman`: `kalman.update()` — expected <100us
- `t_fsm`: `fsm.update()` — expected <50us
- `t_power`: `power.read_all()` — expected <200us (3 ADC reads)
- `t_pack`: `struct.pack_into()` — expected <50us
- `t_total`: end-to-end frame time

**Output**:
```
Pipeline Budget (1000 frames, 25 Hz = 40000 us):

  Stage         Avg(us)  Max(us)  % Budget
  ──────────────────────────────────────────
  Baro read     31200    34100    78.0%
  Alt calc         15       22     0.0%
  Kalman           45       62     0.1%
  FSM              12       18     0.0%
  Power read       85      120     0.2%
  Struct pack      18       25     0.0%
  ──────────────────────────────────────────
  TOTAL         31375    34200    78.4%
  Headroom       8625     5800    21.6%
```

---

### 4. RAM Profile

**What**: Memory usage tracking + leak detection over 1000 hot-loop iterations.

**Why**: MicroPython's GC can cause surprising pauses and fragmentation.
Need to verify the hot loop doesn't allocate (CLAUDE.md rule: "never allocate
memory in the hot loop"). The laptop has unlimited RAM so leaks are invisible.

**Measures**:
- Baseline: `gc.mem_free()` after imports, after object creation
- Object sizes: bracket each allocation with `gc.collect()` + `mem_free()`
  - AltitudeKalman, FlightStateMachine, FlightLogger, BMP280
- Hot loop: 1000 iterations of baro→altitude→kalman→fsm
  - Record `gc.mem_free()` after `gc.collect()` every 100 iterations (10 checkpoints)
  - Store 10 readings in a small list to detect downward trend
- Leak verdict: if delta > 100 bytes over 1000 frames, flag as leak

**Output**:
```
RAM Usage:
  Total available:   191,232 bytes
  After imports:      45,200 bytes free
  After objects:      42,800 bytes free

  Object             Size (bytes)
  AltitudeKalman          96
  FlightStateMachine     148
  FlightLogger           280
  BMP280 + cal           204

Hot Loop (1000 frames):
  Iter     Free     Delta
  0        42800    ---
  100      42792    -8
  200      42792     0
  ...
  1000     42788    -4

  Leak rate: ~4 bytes / 1000 frames [OK — negligible]
```

---

### 5. Float Precision

**What**: Kalman filter accumulation drift over 10,000 iterations with
MicroPython's 32-bit floats.

**Why**: CPython uses 64-bit doubles. MicroPython uses 32-bit single-precision.
The Kalman filter accumulates products (`P = F @ P @ F.T + Q`) every frame.
Over thousands of iterations, 32-bit rounding errors could cause the covariance
matrix to lose positive-definiteness or the state estimate to drift. Need to
measure actual drift on the Pico.

**No hardware needed** — pure math test.

**Measures**:
- Test A (constant input): Feed `500.0 m` for 10,000 iterations, dt=0.04s.
  Expected: alt→500.0, vel→0.0. Measure final drift.
- Test B (ramp input): Feed `i * 1.0` for 10,000 iterations. Expected
  velocity ~25 m/s. Measure tracking error.
- Test C (covariance health): Check `p00 >= 0` and `p11 >= 0` at end.
  If either goes negative, the filter is numerically broken.
- Checkpoints every 1000 iterations (10 rows in output).

**Output**:
```
Float Precision — Kalman (10000 iterations, dt=0.04s):

Test A: Constant 500.0 m
  Iter    Alt        Vel        Drift
  1000    499.998    0.002      0.002m
  ...
  10000   500.001   -0.001      0.001m  [OK]

Test B: Ramp 0→10000 m
  Expected vel: 25.0 m/s
  Final: alt=9999.85  vel=24.97
  Alt err: 0.15m  Vel err: 0.03 m/s  [OK]

Covariance: P = [[0.091, 0.045], [0.045, 0.478]]
  Diagonal positive: YES  [OK]
```

---

### 6. Dual-Core Stress

**What**: Measure Core 0 timing jitter with and without Core 1 running.

**Why**: `main.py` runs the sensor loop on Core 0 and LED patterns on Core 1
via `_thread.start_new_thread()`. MicroPython's threading on RP2040 uses the
second hardware core — no GIL, but shared memory bus. Need to verify Core 1
doesn't steal bus cycles and cause timing spikes on Core 0.

**Measures**:
- Phase 1 (baseline): 500 frames on Core 0 only. StreamStats for frame time.
- Phase 2 (dual-core): Start Core 1 with LED pattern loop. 500 frames on
  Core 0. StreamStats for frame time.
- Compare avg, max, std between phases.
- Core 1 liveness: shared `_core1_heartbeat` global updated by Core 1 every
  tick. Core 0 checks it didn't go stale (>500ms old).

**Core 1 stop mechanism**: `_thread` on RP2040 can't join/kill threads.
Core 1 function polls a `_core1_stop` global and returns when set. Same
pattern used in `main.py`.

**Output**:
```
Dual-Core Stress (60 sec):

                Avg(us)   Max(us)   Std(us)
  Core 0 only:  31450     33800     285
  Core 0+1:     31520     34200     310

  Jitter increase: +70us avg, +400us max  [OK — within budget]
  Core 1 alive: 60.0 seconds  [OK]
```

---

### 7. Endurance Run

**What**: Full pipeline for 10 minutes at 25 Hz. Track timing, RAM, and
errors over time.

**Why**: Flight can last 30+ minutes on the pad + flight. Need to verify
nothing degrades — timing stays stable, RAM doesn't leak, no accumulating
errors. Also catches thermal effects (BMP280 self-heating, Pico warming up).

**Measures** (status line every 30 seconds, 20 lines total):
- Frame timing: StreamStats reset per interval
- RAM: `gc.collect()` + `gc.mem_free()` per interval
- Temperature: from BMP280 reads (detect thermal drift)
- Errors: count of I2C read failures (try/except around baro.read())
- SD writes: if SD mounted, write frames and track write errors

**Abort**: Ctrl+C returns to menu at any time. Partial results are printed.

**Output**:
```
Endurance Run (10 min, 25 Hz):

  Time   Avg(us)  Max(us)  RAM(free)  Temp(C)  Errors
  0:30   31480    33900    42780      24.2     0
  1:00   31490    34100    42776      24.3     0
  ...
  10:00  31510    34200    42772      24.5     0

  Total frames: 15000
  Timing drift: +30us (0.1%)  [OK]
  RAM change: -8 bytes  [OK]
  Temp drift: +0.3C  [OK]
  Errors: 0  [OK]
```

---

### 8. Error Injection

**What**: Deliberately cause failures, verify the system recovers.

**Why**: The laptop tests mock failures with `SENSOR_FAULT` sentinels and
monkey-patched `OSError`s. This tests **real** failure modes on real hardware.

**Tests**:

| Test | What it does | Expected |
|------|-------------|----------|
| A: I2C wrong address | Read from 0x50 (no device) | `OSError`, then BMP280 at 0x77 still works |
| B: I2C bus recovery | Re-create `SoftI2C` object | BMP280 responds after re-init |
| C: SD unmount/remount | Write → `unmount()` → write (fail) → `mount()` → write (ok) | Graceful fail + recovery |
| D: Kalman bad input | Feed `float('inf')`, `float('nan')`, `1e15` | No crash (may return garbage) |
| E: FSM extreme values | Feed alt=99999, vel=99999 | No crash, valid state returned |

**Safety**: Test B (bus recovery) wraps pin manipulation in `try/finally`
to always re-init SoftI2C, even on exception.

**Output**:
```
Error Injection:

  I2C wrong address:   OSError caught, BMP280 OK after  [PASS]
  I2C bus recovery:    Re-init success, BMP280 OK after  [PASS]
  SD unmount/remount:  Write fail expected, remount OK   [PASS]
  Kalman inf input:    No crash, returned (inf, nan)     [PASS]
  FSM extreme values:  No crash, state=BOOST             [PASS]
```

---

## Menu

```
  MPR ALTITUDE LOGGER — DIAGNOSTIC TUI
  RP2040 @ 200 MHz

  1. Sensor Bench      BMP280 timing + noise (1000 reads)
  2. SD Card Bench     Write/flush latency (5 min sustained)
  3. Loop Budget       Per-stage pipeline timing (1000 frames)
  4. RAM Profile       Memory usage + leak detection
  5. Float Precision   Kalman drift over 10000 iterations
  6. Dual-Core Stress  Core 0+1 interference (60 sec)
  7. Endurance Run     Full pipeline stability (10 min)
  8. Error Injection   Fault tolerance verification
  0. Exit

  Select [0-8]:
```

Hardware init is lazy — deferred until the first test that needs it. Test 5
(Float Precision) needs no hardware at all.

---

## RAM Budget

Worst-case simultaneous memory during any single test:

| Item | Bytes |
|------|-------|
| Module code (~400 lines) | ~8,000 |
| Imported modules (config, barometer, kalman, etc.) | ~12,000 |
| BMP280 + calibration data | ~200 |
| PowerMonitor + 3 ADC objects | ~150 |
| AltitudeKalman | ~100 |
| FlightStateMachine | ~150 |
| StreamStats (max 6 in Loop Budget) | 168 |
| Histogram (1 at a time) | 100 |
| Write buffer (34 bytes) | 34 |
| Small checkpoint list (10 values) | 80 |
| String formatting overhead | ~500 |
| **Total working set** | **~21,500** |

Leaves 40KB+ free. Comfortable.

---

## Gotchas

- **`_thread` can't join/kill** — Core 1 must poll a stop flag and return
- **`input()` blocks on USB serial** — fine for menu, but Pico is unresponsive until you type
- **BMP280 read = ~31ms** — 1000 reads = ~31 seconds. Show progress dots
- **SD temp file** — if power lost mid-test, `/sd/_diag_bench.tmp` left behind (harmless)
- **`os.sync()` may not exist** — check `hasattr(os, 'sync')`, skip gracefully
- **Error injection test B** — always re-init SoftI2C in `finally` block
- **No `time.monotonic()`** in MicroPython — use `time.ticks_us()` / `ticks_diff()` for all timing
