"""
main.py — MPR Altitude Logger entry point.

DUAL-CORE ARCHITECTURE:
    Core 0: Preflight checks → Sensor reads → Kalman filter → State machine → SD log
    Core 1: LED status patterns

Single boot flow: preflight checks run automatically, then flight mode starts.
LED is the only visual feedback — blinking = running, solid ON = error.

LED Guide (printed to serial on boot):
    Fast blink (250ms) — booting / preflight in progress
    Solid ON           — error (preflight failure)
    Slow blink (1s)    — PAD state, waiting for launch (all good, safe to disconnect)
    Fast blink (50ms) — BOOST detected
    Medium blink      — COAST / DROGUE / MAIN descent
    Double flash      — APOGEE
    Triple flash      — LANDED (data safe)
"""

import time
import struct
from machine import SoftI2C, Pin, freq
import _thread

import config
from sensors.barometer import BMP180, pressure_to_altitude
from sensors.power import PowerMonitor
from flight.kalman import AltitudeKalman
from flight.state_machine import FlightStateMachine, PAD, LANDED, STATE_NAMES
from logging.datalog import FlightLogger, next_log_filename
from logging.sdcard_mount import mount as mount_sd, free_space_mb
from utils.hardware import StatusLED, LED_PATTERNS


# ── Shared state between cores (keep minimal) ───────────────
_current_state = PAD
_landed = False
_error_mode = False  # solid LED = error


def core1_task():
    """
    Core 1: LED status patterns.

    Runs in a slower loop (~20 Hz). Reads shared state set by Core 0.
    """
    global _current_state, _landed, _error_mode

    led = StatusLED()
    led.set_pattern([250, 250])  # fast blink = preflight running

    last_state = -1

    while True:
        now = time.ticks_ms()

        # Error mode: solid ON
        if _error_mode:
            led.on()
            time.sleep_ms(50)
            continue

        # Update LED pattern on state change
        if _current_state != last_state:
            last_state = _current_state
            pattern = LED_PATTERNS.get(_current_state)
            if pattern is None:
                led.on()
            else:
                led.set_pattern(pattern)

        led.tick(now)
        time.sleep_ms(50)  # ~20 Hz


def core0_main():
    """
    Core 0: preflight → sensor loop → filter → state machine → logger.
    """
    global _current_state, _landed, _error_mode

    print("\n╔══════════════════════════════════════════╗")
    print("║   UNSW ROCKETRY — MPR ALTITUDE LOGGER    ║")
    print("╚══════════════════════════════════════════╝\n")

    # ── LED Guide ─────────────────────────────────────
    print("LED GUIDE:")
    print("  Fast blink (250ms) = booting / preflight running")
    print("  Solid ON           = ERROR (check serial)")
    print("  Slow blink (1s)    = PAD — ready, safe to disconnect USB")
    print("  Fast blink       = BOOST")
    print("  Medium blink     = COAST / descent")
    print("  Double flash     = APOGEE")
    print("  Triple flash     = LANDED — data saved")
    print()

    # ── Overclock for headroom ────────────────────────
    freq(200_000_000)
    print(f"[1/7] Overclock        {freq() // 1_000_000} MHz")

    # ── Start Core 1 early so LED works during preflight ──
    _thread.start_new_thread(core1_task, ())
    print("[2/7] LED started      slow blink = preflight running")

    # ── Preflight checks ──────────────────────────────
    preflight_errors = []
    step = 3

    # SD card
    print(f"[{step}/7] SD card ...", end="")
    if not mount_sd():
        preflight_errors.append("SD card mount failed")
        print("     FAIL — mount failed")
    else:
        free_mb = free_space_mb()
        next_file = next_log_filename(config.LOG_FILENAME)
        print(f"     OK — {free_mb:.0f} MB free → {next_file}")
    step += 1

    # Barometer
    print(f"[{step}/7] Barometer ...", end="")
    baro = None
    try:
        i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                      freq=config.I2C_FREQ)
        baro = BMP180(i2c, config.BMP180_ADDR)
        p, t = baro.read()
        print(f"  OK — {p:.0f} Pa, {t:.1f}°C")
    except Exception as e:
        preflight_errors.append(f"Barometer: {e}")
        print(f"  FAIL — {e}")
    step += 1

    # Power rails
    print(f"[{step}/7] Power rails ...", end="")
    power = PowerMonitor()
    warnings = power.check_health()
    v3, v5, v9 = power.read_all()
    if warnings:
        print(f" WARN")
        for w in warnings:
            print(f"         {w}")
    else:
        print(f" OK — 3V3={v3}mV 5V={v5}mV 9V={v9}mV")
    step += 1

    # ── Handle preflight failures ─────────────────────
    if preflight_errors:
        _error_mode = True  # solid LED = error
        print()
        print("╔══════════════════════════════════════════╗")
        print("║  PREFLIGHT FAILED — LED IS SOLID ON      ║")
        print("╠══════════════════════════════════════════╣")
        for e in preflight_errors:
            print(f"║  - {e:<38s} ║")
        print("╠══════════════════════════════════════════╣")
        print("║  Ctrl-C to abort, or wait 10s to proceed ║")
        print("╚══════════════════════════════════════════╝")
        for countdown in range(10, 0, -1):
            print(f"  {countdown}...")
            time.sleep(1)
        _error_mode = False
        print("  Proceeding despite errors.")
        print()

    # ── Critical failures that prevent logging ────────
    sd_ok = "SD card" not in str(preflight_errors)
    if not sd_ok:
        print("[FATAL] Cannot log without SD card. LED will stay solid.")
        print("        Power cycle after inserting SD card.")
        _error_mode = True
        while True:
            time.sleep(1)

    if baro is None:
        print("[FATAL] Cannot fly without barometer. LED will stay solid.")
        print("        Check I2C wiring and power cycle.")
        _error_mode = True
        while True:
            time.sleep(1)

    # Ground calibration
    print(f"[{step}/7] Calibrating ...", end="")
    pressure_sum = 0.0
    for i in range(config.GROUND_SAMPLES):
        p, t = baro.read()
        pressure_sum += p
        time.sleep_ms(20)
    ground_pressure = pressure_sum / config.GROUND_SAMPLES
    print(f" OK — ground P={ground_pressure:.0f} Pa")
    step += 1

    kalman = AltitudeKalman()
    fsm = FlightStateMachine()
    fsm.set_ground_reference(0.0)
    kalman.reset(0.0)

    # Open logger
    logger = FlightLogger(config.LOG_FILENAME, flush_every=config.LOG_FLUSH_EVERY)
    log_file = logger.open()
    print(f"[{step}/7] Logger open   {log_file}")

    # ── All clear ─────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  ALL PREFLIGHT CHECKS PASSED             ║")
    print("║                                          ║")
    print("║  LED: slow blink = PAD (waiting)         ║")
    print("║  Safe to disconnect USB and seal board   ║")
    print("║                                          ║")
    print(f"║  Logging at {config.SAMPLE_RATE_HZ} Hz to {log_file:<19s}║")
    print("╚══════════════════════════════════════════╝")
    print()
    print("[RDY] Waiting for launch...\n")

    # ── Main sensor loop ──────────────────────────────
    interval_us = 1_000_000 // config.SAMPLE_RATE_HZ
    last_time = time.ticks_us()
    last_print = time.ticks_ms()
    loop_count = 0
    prev_state = PAD

    while True:
        now_us = time.ticks_us()
        dt_us = time.ticks_diff(now_us, last_time)

        # Rate limiting — spin-wait for consistent timing
        if dt_us < interval_us:
            continue

        last_time = now_us
        now_ms = time.ticks_ms()
        dt = dt_us / 1_000_000.0  # seconds

        # ── Read sensors ──────────────────────────
        pressure, temperature = baro.read()
        alt_raw = pressure_to_altitude(pressure, ground_pressure)

        # ── Kalman filter ─────────────────────────
        alt_filt, vel_filt = kalman.update(alt_raw, dt)

        # ── State machine ─────────────────────────
        state = fsm.update(alt_filt, vel_filt, now_ms)

        # Set shared state for Core 1
        _current_state = state
        if state == LANDED:
            _landed = True

        # Flush on state transitions
        if state != prev_state:
            logger.notify_state_change(state)
            prev_state = state

        # ── Build flags byte ──────────────────────
        flags = 0
        if logger.sd_failed:
            flags |= 0x08  # error flag
            _error_mode = True  # solid LED = SD card lost

        # ── Log to SD ────────────────────────────
        v3, v5, v9 = power.read_all()
        logger.write_frame(
            timestamp_ms=now_ms,
            state=state,
            pressure_pa=pressure,
            temperature_c=temperature,
            alt_raw=alt_raw,
            alt_filtered=alt_filt,
            vel_filtered=vel_filt,
            v_3v3_mv=v3,
            v_5v_mv=v5,
            v_9v_mv=v9,
            flags=flags,
        )

        # ── Console output (1 Hz) ────────────────
        loop_count += 1
        if time.ticks_diff(now_ms, last_print) >= 1000:
            hz = loop_count
            loop_count = 0
            last_print = now_ms
            print(
                f"[{STATE_NAMES[state]:7s}] "
                f"alt={alt_filt:7.1f}m  vel={vel_filt:+6.1f}m/s  "
                f"P={pressure:.0f}Pa  T={temperature:.1f}°C  "
                f"3V3={v3}mV  "
                f"{hz}Hz  #{logger.frames_written}"
            )

        # ── Post-landing shutdown ─────────────────
        if state == LANDED and time.ticks_diff(now_ms, fsm.apogee_time) > 30_000:
            # 30s after apogee (well after landing), close the file
            stats = fsm.get_stats()
            print(f"\n[LANDED] Flight complete!")
            print(f"  Max altitude: {stats['max_alt_m']:.1f} m AGL")
            print(f"  Max velocity: {stats['max_vel_ms']:.1f} m/s")
            print(f"  Frames logged: {logger.frames_written}")
            try:
                logger.close()
                print("[LOG] File closed. Safe to remove SD card.")
            except Exception:
                print("[LOG] File close failed — data may be incomplete.")
            # Keep running for LED feedback (triple flash = landed)
            while True:
                time.sleep(1)


# ── Entry point ───────────────────────────────────────────
core0_main()
