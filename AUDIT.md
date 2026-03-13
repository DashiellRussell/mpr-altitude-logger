# Comprehensive Reliability & Data Integrity Audit — MPR Altitude Logger

## Context
Full audit of the flight computer codebase focused on SD card data integrity, failure modes, and hardening opportunities. The #1 priority is protecting flight log data on the SD card.

---

## CRITICAL Issues (Fix Before Next Flight)

### 1. No Exception Handling Around Sensor Reads in Main Loop
**File:** `main.py` ~lines 314-372

The main sensor loop calls `baro.read()`, `kalman.update()`, `fsm.update()`, and `power.read_all()` with **zero try/except**. Any exception (I2C timeout, math error, bad read) crashes Core 0 entirely — logging dies, LED goes dark, mission lost.

**Fix:** Wrap the sensor chain in try/except. On error, log a frame with error flag set and continue. Never let the logging loop crash.

---

### 2. `logger.open()` Has No Exception Handling
**File:** `main.py` ~line 250

If `open()` fails (SD ejected, full disk, permission error), the exception propagates uncaught and **crashes the entire flight computer** before flight mode even begins. The logger object is left partially initialized.

**Fix:** Wrap in try/except, set `logger = None` on failure, and enter error mode with LED indication.

---

### 3. Sync Header and Frame Are Two Separate Writes
**File:** `logging/datalog.py` ~lines 169-170

```python
self._file.write(FRAME_HEADER)  # 2 bytes
self._file.write(frame)          # 32 bytes
```

Power loss between these two writes leaves orphaned sync bytes — a malformed frame. The decoder skips it, but you lose that data point.

**Fix:** Concatenate header + frame into a single `write()` call: `self._file.write(FRAME_HEADER + frame)`.

---

### 4. `os.mkdir()` Swallows ALL OSErrors Silently
**File:** `logging/datalog.py` ~lines 101-104

```python
try:
    os.mkdir(flight_dir)
except OSError:
    pass  # meant to catch "already exists", catches EVERYTHING
```

This silently eats "disk full", "read-only", "I/O error". The subsequent `open()` then fails in a less predictable way.

**Fix:** Catch only `errno.EEXIST`, or verify the directory exists after the call.

---

### 5. `_sd_failed = True` Is Permanent — No Retry
**File:** `logging/datalog.py` ~lines 183-186

Once any write exception occurs, `_sd_failed` is set and **logging is dead for the rest of the flight**. If the SD card had a momentary contact issue (vibration), it never retries.

**Fix:** Implement retry logic — attempt N retries with increasing backoff before giving up permanently. Even 1 retry would help.

---

### 6. `os.sync()` Silently No-Ops on Some MicroPython Builds
**File:** `logging/datalog.py` ~lines 36-41

```python
def _try_sync():
    try:
        os.sync()
    except AttributeError:
        pass  # silently does nothing
```

If `os.sync()` isn't available, FAT metadata is **never forced to disk**. A power loss means the file may not even appear in the directory listing.

**Fix:** Check for `os.sync` availability at boot and log a **warning**. Consider it a preflight failure if unavailable.

---

### 7. Kalman Filter Covariance Can Go Negative
**File:** `flight/kalman.py` ~lines 83-111

The standard-form covariance update can produce negative P values under large innovations (sensor glitches). Once P is negative, the filter diverges — velocity estimates become garbage, and the state machine makes wrong transitions (false apogee, stuck states).

**Fix:** Use Joseph form update, or clamp diagonal P elements to >= 0 after each update step.

---

### 8. No Watchdog Timer — Hangs Are Unrecoverable
**File:** entire codebase

The RP2040 has a hardware watchdog, but it's never used. If Core 0 hangs (I2C deadlock, infinite loop bug), the Pico freezes forever. No data, no LED, no recovery.

**Fix:** Enable `machine.WDT(timeout=5000)` and feed it each loop iteration. On timeout, the Pico reboots and at least starts a new log file.

---

### 9. I2C Has No Timeout — BMP Sensor Can Lock the Bus
**File:** `sensors/barometer.py` ~lines 68-81

`i2c.readfrom_mem()` and `i2c.writeto_mem()` have no timeout. If the BMP holds SDA low (power glitch, ESD, cold solder joint), Core 0 blocks **forever**.

**Fix:** Use SoftI2C with timeout parameter, or wrap I2C calls with a manual timeout using `ticks_ms`.

---

## HIGH Issues

### 11. File Name Override Can Silently Overwrite Previous Flight Data
**File:** `logging/datalog.py` ~lines 66-68

If `/sd/_flight_name.txt` contains a stale name from a previous flight, the logger opens that flight's `flight.bin` in `'wb'` mode and **truncates it** — previous flight data destroyed.

**Fix:** Check if the target directory already contains a `flight.bin` before opening. Or delete the override file after reading.

---

### 12. 10-Second Sync Interval — Up to 10s of Data at Risk
**File:** `logging/datalog.py` (sync_every=10) and `config.py` (LOG_FLUSH_EVERY=25)

At 25 Hz with flush every 25 frames and sync every 10 flushes, FAT metadata is only committed every ~10 seconds. Power loss within that window risks:
- File size metadata wrong (decoder truncates early)
- Directory entries incomplete
- Last ~250 frames (10s) potentially unrecoverable

**Fix:** Reduce `sync_every` to 2-3 (sync every 2-3 seconds). The write overhead is minimal.

---

### 13. Ground Reference Always Set to 0.0
**File:** `main.py` ~line 247

```python
fsm.set_ground_reference(0.0)  # Always 0, regardless of launch site elevation
```

All state machine thresholds are checked against absolute altitude, not AGL. At a high-elevation launch site (e.g., 500m), launch detection threshold of 15m means the rocket needs to reach 515m absolute.

**Fix:** This is actually fine *if* the Kalman filter is reset to 0.0 at ground level (which it is). The altitudes coming into the FSM are already relative to ground. Verify this is the case by tracing the data flow — if `pressure_to_altitude()` returns absolute altitude, then the ground reference needs to be set to that value.

---

### 14. APOGEE State Lasts Only 1 Frame
**File:** `flight/state_machine.py` ~lines 122-124

APOGEE transitions to DROGUE on the very next `update()` call — it exists for only one frame (40ms). If that frame is lost to an SD write failure, the apogee event is invisible in post-flight data.

**Fix:** Add a minimum dwell time in APOGEE (e.g., 5 frames / 200ms) before transitioning to DROGUE.

---

### 15. Preflight Error Mode Gets Silently Cleared
**File:** `main.py` ~line 206

After a 10-second countdown on preflight errors, `_error_mode = False` is set and flight continues. The LED stops showing the error. If someone checks the rocket at this point, the LED says "all good" — but preflight failed.

**Fix:** Keep a separate `_preflight_warning` flag that persists, or flash a distinct pattern (e.g., double-blink) to indicate "running with warnings."

---

### 16. Manual Override + Barometer Fail = Silent Zero-Data Flight
**File:** `main.py` ~lines 221-228

With manual override active, a barometer failure is demoted to a warning. The main loop then hits `if baro is None: continue` — no data is ever logged. The SD card gets a valid file with a header and zero frames. Completely silent mission failure.

**Fix:** If barometer is None, this should be fatal regardless of manual override. The barometer is the only sensor — without it, there's nothing to log.

---

### 17. SD Mount Can Hang Forever
**File:** `logging/sdcard_mount.py` ~lines 16-48

`SDCard()` initialization and SPI operations have no timeout. A partially inserted or damaged SD card can block the boot sequence indefinitely.

**Fix:** Add a timeout wrapper or use the watchdog timer to recover.

---

### 18. Brownout Mid-Flight Causes Invisible Data Discontinuity
**File:** general architecture

If the Pico reboots mid-flight: all state resets, ground recalibrates at current altitude, timestamp resets to 0, new flight log file is created. The decoder sees two separate files with no indication they're from the same flight.

**Fix:** Write a reboot marker (e.g., special state byte = 0xFF) or store a monotonic flight ID. At minimum, document this behavior for post-flight analysis.

---

## MEDIUM Issues

### 19. `write_frame()` Exception Handler Is Too Broad
Catches `Exception` (including `MemoryError`, `KeyboardInterrupt`). Any error kills logging permanently.

### 20. `unmount()` Uses Bare `except:`
Swallows `SystemExit` and `KeyboardInterrupt`. Can leave mount state inconsistent.

### 21. `is_mounted()` Check Races with Write Operations
Uses `os.listdir('/sd')` which can fail during active writes, returning false negatives.

### 22. No ADC Input Validation on Power Monitor
`read_u16()` returning 0 or 65535 (rail failures) is silently logged as valid data.

### 23. BOOST Recovery Window Asymmetry
Launch detection requires 0.5s sustained, but recovery triggers on a single frame of low altitude. A jittery sensor can cause false launch → immediate recovery loops.

### 24. Apogee Detection Hysteresis
Velocity oscillating around the 2.0 m/s threshold resets the confirmation counter. At turnaround, velocity crosses quickly — only 1-2 frames below threshold before the counter resets.

### 25. No Config Validation at Startup
Negative Kalman Q/R, overlapping pin assignments, inconsistent thresholds — all silently accepted.

### 26. No Loop Timing Jitter Detection
If a sensor read takes 50ms instead of 10ms, the Kalman filter's `dt` parameter is wrong for that cycle. No warning or logging.

### 27. OpenRocket Importer: Fragile Unit Detection
`Altitude [m]` (square brackets) silently assumes SI. Could produce 3.28x altitude errors with feet data.

### 28. hw_check.py Tests SD at 1 MHz but Flight Runs at 10 MHz
SD card might work at 1 MHz but fail at 10 MHz under vibration/noise.

### 29. No Dual-Core Test in hw_check or ground_test
Core 1 startup is never validated before flight.

---

## Recommended Priority Order

**Before next flight (critical path):**
1. Wrap main loop sensor reads in try/except (#1)
2. Wrap `logger.open()` in try/except (#2)
3. Combine sync header + frame into single write (#3)
4. Add watchdog timer (#8)
5. Make barometer failure always fatal (#16)

**Before competition (high priority):**
6. Add I2C timeout handling (#9)
7. Fix `os.mkdir()` error swallowing (#4)
8. Add SD write retry logic (#5)
9. Reduce sync interval to 2-3s (#12)
10. Warn on missing `os.sync()` (#6)
11. Clamp Kalman covariance (#7)
12. Fix file name override overwrite risk (#11)
13. Add config validation at startup (#25)

**Nice to have:**
14. Add APOGEE dwell time (#14)
15. Add reboot detection (#18)
16. Fix preflight error LED behavior (#15)
17. Improve ADC validation (#22)
18. Test SD at production SPI speed (#28)

---

## Verification Plan

After implementing fixes:
1. Run `hw_check.py` — all checks pass
2. Run `ground_test.py` — all subsystems nominal
3. Simulate SD card failure (remove card mid-test) — verify retry logic and graceful degradation
4. Simulate I2C failure (disconnect barometer) — verify watchdog triggers reboot, error LED shows
5. Simulate power loss (pull power mid-log) — verify decoder can recover all frames up to last sync
6. Run decode_log.py on resulting .bin files — verify no corruption or misinterpretation
7. Shake test (physically shake the board while logging) — verify no I2C lockups or SD contact issues
8. Full ground-to-ground simulation using `simulate.py` data fed through the system
