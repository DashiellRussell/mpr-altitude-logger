"""
main.py — MPR Altitude Logger entry point.

ARCHITECTURE:
    Core 0: Preflight checks → Sensor reads → Kalman filter → State machine → SD log
    LED:    Timer callback (soft IRQ) — no _thread, no cross-core GIL contention

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

import sys
import os
import time
import struct
from machine import SoftI2C, Pin, freq, WDT
import config
from sensors.barometer import BMP180, pressure_to_altitude
from sensors.power import PowerMonitor
from flight.kalman import AltitudeKalman
from flight.state_machine import FlightStateMachine, PAD, LANDED, STATE_NAMES
from logging.datalog import FlightLogger, next_log_filename
from logging.sdcard_mount import mount as mount_sd, free_space_mb
from utils.hardware import TimerLED, LED_PATTERNS


# ── Shared state ─────────────────────────────────────────────
_current_state = PAD
_landed = False

# ── Boot log capture ────────────────────────────────────────
_boot_log = []

def blog(msg, end='\n'):
    """Print and capture boot output for saving to SD card."""
    _boot_log.append(msg if end == '\n' else msg)
    print(msg, end=end)


def core0_main():
    """
    Core 0: preflight → sensor loop → filter → state machine → logger.
    """
    global _current_state, _landed

    blog("\n╔══════════════════════════════════════════╗")
    blog("║   UNSW ROCKETRY — MPR ALTITUDE LOGGER    ║")
    blog("╚══════════════════════════════════════════╝\n")

    # ── LED Guide ─────────────────────────────────────
    blog("LED GUIDE:")
    blog("  Fast blink (250ms) = booting / preflight running")
    blog("  Solid ON           = ERROR (check serial)")
    blog("  Slow blink (1s)    = PAD — ready, safe to disconnect USB")
    blog("  Fast blink       = BOOST")
    blog("  Medium blink     = COAST / descent")
    blog("  Double flash     = APOGEE")
    blog("  Triple flash     = LANDED — data saved")
    blog("")

    # ── Config validation ───────────────────────────────
    config.validate()

    # ── Overclock for headroom ────────────────────────
    freq(200_000_000)
    blog(f"[1/7] Overclock        {freq() // 1_000_000} MHz")

    # ── LED via hardware Timer (no _thread — avoids GIL contention) ──
    led = TimerLED()  # virtual timer, 25ms tick
    led.set_pattern([250, 250])  # fast blink = preflight running
    blog("[2/7] LED started      fast blink = preflight running")

    # ── Hardware watchdog (5s timeout) ──────────────────
    wdt = WDT(timeout=5000)

    # ── Preflight checks ──────────────────────────────
    preflight_errors = []
    step = 3

    # SD card (3 attempts — SD cards can be flaky on cold boot)
    blog(f"[{step}/7] SD card ...", end="")
    sd_mounted = False
    for attempt in range(3):
        wdt.feed()
        if mount_sd():
            sd_mounted = True
            break
        if attempt < 2:
            from logging.sdcard_mount import unmount as unmount_sd
            unmount_sd()  # Clean up before retry
            blog(f" retry {attempt + 2}/3...", end="")
            time.sleep(1)
    wdt.feed()
    if not sd_mounted:
        preflight_errors.append("SD card mount failed")
        blog("     FAIL — mount failed (3 attempts)")
    else:
        free_mb = free_space_mb()
        next_file = next_log_filename(config.LOG_FILENAME)
        blog(f"     OK — {free_mb:.0f} MB free → {next_file}")
    step += 1

    # Barometer (3 attempts)
    blog(f"[{step}/7] Barometer ...", end="")
    baro = None
    last_baro_err = None
    for attempt in range(3):
        wdt.feed()
        try:
            i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                          freq=config.I2C_FREQ, timeout=config.I2C_TIMEOUT_US)
            baro = BMP180(i2c, config.BMP180_ADDR)
            p, t = baro.read()
            blog(f"  OK — {p:.0f} Pa, {t:.1f}°C")
            break
        except Exception as e:
            last_baro_err = e
            baro = None
            if attempt < 2:
                blog(f" retry {attempt + 2}/3...", end="")
                wdt.feed()
                time.sleep_ms(500)
    if baro is None:
        preflight_errors.append(f"Barometer: {last_baro_err}")
        blog(f"  FAIL — {last_baro_err}")
    step += 1

    # Power rails
    blog(f"[{step}/7] Power rails ...", end="")
    power = PowerMonitor()
    warnings = power.check_health()
    v3, v5, v9 = power.read_all()
    if warnings:
        blog(f" WARN")
        for w in warnings:
            blog(f"         {w}")
    else:
        blog(f" OK — 3V3={v3}mV 5V={v5}mV 9V={v9}mV")
    step += 1

    # ── Check for manual override flag from TUI ──────
    manual_override = False
    try:
        os.stat('_manual_override')
        manual_override = True
        os.remove('_manual_override')  # one-shot, consume after reading
        blog("  [OVERRIDE] Manual override active — skipping fatal halts")
    except OSError:
        pass

    # ── Handle preflight failures ─────────────────────
    if preflight_errors:
        led.on()  # solid LED = error
        blog("")
        blog("╔══════════════════════════════════════════╗")
        blog("║  PREFLIGHT FAILED — LED IS SOLID ON      ║")
        blog("╠══════════════════════════════════════════╣")
        for e in preflight_errors:
            blog(f"║  - {e:<38s} ║")
        blog("╠══════════════════════════════════════════╣")
        if manual_override:
            blog("║  MANUAL OVERRIDE — proceeding immediately ║")
        else:
            blog("║  Ctrl-C to abort, or wait 10s to proceed ║")
        blog("╚══════════════════════════════════════════╝")
        if not manual_override:
            for countdown in range(10, 0, -1):
                wdt.feed()
                blog(f"  {countdown}...")
                time.sleep(1)
        led.set_pattern([200, 200, 200, 1000])  # warning pattern for PAD
        blog("  Proceeding despite errors.")
        blog("")

    # ── Critical failures that prevent logging ────────
    # SD card is ALWAYS fatal — no point launching if we can't log data
    sd_ok = "SD card" not in str(preflight_errors)
    if not sd_ok:
        blog("[FATAL] Cannot log without SD card. LED will stay solid.")
        blog("        Power cycle after inserting SD card.")
        led.on()
        while True:
            wdt.feed()
            time.sleep(1)

    if baro is None:
        blog("[FATAL] Cannot fly without barometer. LED will stay solid.")
        blog("        Check I2C wiring and power cycle.")
        led.on()
        while True:
            wdt.feed()
            time.sleep(1)

    # Ground calibration
    ground_pressure = 101325.0  # default sea level if baro failed
    if baro is not None:
        blog(f"[{step}/7] Calibrating ...", end="")
        pressure_sum = 0.0
        for i in range(config.GROUND_SAMPLES):
            wdt.feed()
            p, t = baro.read()
            pressure_sum += p
            time.sleep_ms(20)
        ground_pressure = pressure_sum / config.GROUND_SAMPLES
        blog(f" OK — ground P={ground_pressure:.0f} Pa")
    else:
        blog(f"[{step}/7] Calibrating    SKIP — no barometer")
    step += 1

    kalman = AltitudeKalman()
    fsm = FlightStateMachine()
    fsm.set_ground_reference(0.0)
    kalman.reset(0.0)

    # Open logger (creates per-flight folder)
    logger = None
    log_file = 'NONE'
    if sd_ok:
        try:
            logger = FlightLogger(flush_every=config.LOG_FLUSH_EVERY,
                                  sync_every=config.LOG_SYNC_EVERY,
                                  wdt=wdt)
            log_file = logger.open()
            blog(f"[{step}/7] Logger open   {log_file}")
        except Exception as e:
            logger = None
            log_file = 'NONE'
            led.on()
            blog(f"[{step}/7] Logger open   FAIL — {e}")
    else:
        blog(f"[{step}/7] Logger open   SKIP — no SD card")

    # Write preflight metadata to flight folder
    preflight_lines = [
        'UNSW Rocketry — MPR Altitude Logger',
        'Avionics v{}'.format(config.VERSION),
        'MicroPython {}'.format(sys.version),
        'Boot time: {} ms'.format(time.ticks_ms()),
        '',
        '--- Preflight Results ---',
        'Manual override: {}'.format('YES' if manual_override else 'NO'),
        'Errors: {}'.format(', '.join(preflight_errors) if preflight_errors else 'None'),
        'Ground pressure: {:.0f} Pa'.format(ground_pressure),
        'Voltages: 3V3={}mV 5V={}mV 9V={}mV'.format(v3, v5, v9),
    ]
    if warnings:
        preflight_lines.append('Voltage warnings: {}'.format(', '.join(warnings)))
    if baro is not None:
        preflight_lines.append('Barometer: {:.0f} Pa, {:.1f} C'.format(p, t))
    else:
        preflight_lines.append('Barometer: FAILED')
    preflight_lines.append('Sample rate: {} Hz'.format(config.SAMPLE_RATE_HZ))
    preflight_lines.append('Log file: {}'.format(log_file))
    if logger is not None:
        logger.write_preflight('\n'.join(preflight_lines))

    # ── All clear ─────────────────────────────────────
    blog("")
    if not preflight_errors:
        led.set_pattern(LED_PATTERNS[PAD])  # slow blink = ready
        blog("╔══════════════════════════════════════════╗")
        blog("║  ALL PREFLIGHT CHECKS PASSED             ║")
        blog("║                                          ║")
        blog("║  LED: slow blink = PAD (waiting)         ║")
        blog("║  Safe to disconnect USB and seal board   ║")
        blog("║                                          ║")
        blog(f"║  Logging at {config.SAMPLE_RATE_HZ} Hz to {log_file:<19s}║")
        blog("╚══════════════════════════════════════════╝")
    else:
        blog("╔══════════════════════════════════════════╗")
        blog("║  RUNNING WITH MANUAL OVERRIDE            ║")
        blog(f"║  Logging at {config.SAMPLE_RATE_HZ} Hz to {log_file:<19s}║")
        blog("╚══════════════════════════════════════════╝")
    blog("")
    blog("[RDY] Waiting for launch...\n")

    # ── Save boot log to SD card ────────────────────
    wdt.feed()
    if logger is not None:
        logger.write_boot_log(_boot_log)
    wdt.feed()

    # ── Main sensor loop (pipelined baro reads) ────────
    # Pipeline: pressure conversion runs during the spin-wait between frames.
    # collect() at frame start is just an I2C register read (~1ms, no sleep).
    # Temperature re-read every ~1s (blocking 5ms, easily fits in budget).
    interval_us = 1_000_000 // config.SAMPLE_RATE_HZ
    last_time = time.ticks_us()
    last_print = time.ticks_ms()
    loop_count = 0
    frame_us_sum = 0
    prev_state = PAD
    temp_every = config.SAMPLE_RATE_HZ  # re-read temp once per second
    temp_counter = 0

    # Pre-allocate defaults for error frame logging
    pressure = 0.0
    temperature = 0.0
    alt_raw = 0.0
    alt_filt = 0.0
    vel_filt = 0.0
    v3 = 0
    v5 = 0
    v9 = 0

    # Pipeline priming — blocking reads to get initial values, then kick off
    # the first async pressure conversion that will be collected next frame.
    raw_UT = baro._read_raw_temp()
    baro.start(temp=False)

    while True:
        now_us = time.ticks_us()
        dt_us = time.ticks_diff(now_us, last_time)

        # Rate limiting — spin-wait for consistent timing
        if dt_us < interval_us:
            continue

        last_time = now_us
        now_ms = time.ticks_ms()
        dt = dt_us / 1_000_000.0  # seconds

        wdt.feed()

        try:
            # ── Collect pressure (conversion already done during spin-wait) ──
            raw_UP = baro.collect()
            pressure, temperature = baro.compensate(raw_UT, raw_UP)

            # Re-read temperature every ~1s (blocking 5ms — fits in budget)
            temp_counter += 1
            if temp_counter >= temp_every:
                temp_counter = 0
                raw_UT = baro._read_raw_temp()

            # Kick off next pressure conversion — runs during remaining
            # frame work + spin-wait.  OSS=2 needs 13.5ms, budget is ~18ms.
            baro.start(temp=False)

            alt_raw = pressure_to_altitude(pressure, ground_pressure)

            # ── Kalman filter ─────────────────────────
            alt_filt, vel_filt = kalman.update(alt_raw, dt)

            # ── State machine ─────────────────────────
            state = fsm.update(alt_filt, vel_filt, now_ms)

            # Update shared state
            _current_state = state
            if state == LANDED:
                _landed = True

            # Flush + LED update on state transitions
            if state != prev_state:
                led.set_pattern(LED_PATTERNS.get(state, [1000, 1000]))
                if logger is not None:
                    logger.notify_state_change(state)
                prev_state = state

            # ── Build flags byte ──────────────────────
            flags = 0
            if logger is not None and logger.sd_failed:
                flags |= 0x08  # error flag
                led.on()  # solid LED = SD card lost

            # ── Log to SD ────────────────────────────
            v3, v5, v9 = power.read_all()
            if logger is not None:
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

            # ── Measure frame time (for 1 Hz print) ──
            frame_end_us = time.ticks_us()
            frame_us_sum += time.ticks_diff(frame_end_us, now_us)

            # ── Console output (1 Hz) ────────────────
            loop_count += 1
            if time.ticks_diff(now_ms, last_print) >= 1000:
                hz = loop_count
                avg_frame_us = frame_us_sum // hz if hz else 0
                loop_count = 0
                frame_us_sum = 0
                last_print = now_ms
                frames = logger.frames_written if logger is not None else 0
                print(
                    f"[{STATE_NAMES[state]:7s}] "
                    f"alt={alt_filt:7.1f}m  vel={vel_filt:+6.1f}m/s  "
                    f"P={pressure:.0f}Pa  T={temperature:.1f}°C  "
                    f"3V3={v3}mV  "
                    f"{hz}Hz {avg_frame_us}us  #{frames}"
                )

            # ── Post-landing shutdown ─────────────────
            if state == LANDED and time.ticks_diff(now_ms, fsm.apogee_time) > 30_000:
                # 30s after apogee (well after landing), close the file
                stats = fsm.get_stats()
                print(f"\n[LANDED] Flight complete!")
                print(f"  Max altitude: {stats['max_alt_m']:.1f} m AGL")
                print(f"  Max velocity: {stats['max_vel_ms']:.1f} m/s")
                if logger is not None:
                    print(f"  Frames logged: {logger.frames_written}")
                    try:
                        logger.close()
                        print("[LOG] File closed. Safe to remove SD card.")
                    except Exception:
                        print("[LOG] File close failed — data may be incomplete.")
                else:
                    print("  No SD logging was active.")
                # Keep running for LED feedback (triple flash = landed)
                while True:
                    wdt.feed()
                    time.sleep(1)

        except Exception:
            # Sensor/filter/FSM error — log error frame and continue
            if logger is not None:
                logger.write_frame(
                    timestamp_ms=now_ms,
                    state=prev_state,
                    pressure_pa=0.0,
                    temperature_c=0.0,
                    alt_raw=0.0,
                    alt_filtered=0.0,
                    vel_filtered=0.0,
                    v_3v3_mv=0,
                    v_5v_mv=0,
                    v_9v_mv=0,
                    flags=0x08,
                )
            continue


# ── Entry point ───────────────────────────────────────────
core0_main()
