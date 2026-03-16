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
import gc
import time
import struct
from machine import SoftI2C, Pin, freq, WDT, ADC, reset_cause
import config
from sensors.barometer import BMP180, pressure_to_altitude
from sensors.power import PowerMonitor
from flight.kalman import AltitudeKalman
from flight.state_machine import FlightStateMachine, PAD, LANDED, STATE_NAMES
from logging.datalog import FlightLogger, next_log_filename, read_crash_report
from logging.sdcard_mount import mount as mount_sd, free_space_mb
from utils.hardware import TimerLED, LED_PATTERNS

# ── Non-blocking stdout (USB CDC blocks when host isn't reading) ──
# On RP2040 MicroPython, sys.stdout.write() blocks when the USB CDC buffer
# fills and no host is reading. select.poll() doesn't reliably detect this.
# Solution: measure the first print in the sensor loop. If it takes >50ms,
# the buffer is backing up — disable all further console output.
_console_enabled = True

def nb_print(msg):
    """Print only if console is enabled. Gets disabled if print blocks."""
    if _console_enabled:
        print(msg)


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
    global _current_state, _landed, _console_enabled

    # ── Check for crash reboot ─────────────────────────
    # WDT_RESET = 3 on MicroPython RP2040
    _reboot_reason = reset_cause()
    _crash_data = read_crash_report()
    _is_crash_reboot = (_reboot_reason == 3) and (_crash_data is not None)

    if _is_crash_reboot:
        blog("\n[CRASH REBOOT] Previous session ended in WDT reset")
        blog(f"  Last frame: #{_crash_data['frame_count']}  "
             f"t={_crash_data['timestamp_ms']}ms  "
             f"RAM={_crash_data['free_ram']}B  "
             f"flush={_crash_data['flush_us']}us")
        blog("  Fast-rebooting to resume logging...\n")
    else:
        blog("\n╔══════════════════════════════════════════╗")
        blog("║   UNSW ROCKETRY — MPR ALTITUDE LOGGER    ║")
        blog("╚══════════════════════════════════════════╝\n")

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
    if not _is_crash_reboot:
        blog(f"[1/7] Overclock        {freq() // 1_000_000} MHz")

    # ── LED via hardware Timer (no _thread — avoids GIL contention) ──
    led = TimerLED()  # virtual timer, 25ms tick
    led.set_pattern([250, 250])  # fast blink = preflight running

    # ── Hardware watchdog (5s timeout) ──────────────────
    wdt = WDT(timeout=5000)

    # ── Internal temp sensor (ADC4) ─────────────────────
    _temp_adc = ADC(4)

    # ── Init hardware (always needed, crash or normal) ──
    # SD card — on crash reboot, keep retrying (card was working seconds ago)
    sd_mounted = False
    _sd_max_attempts = 30 if _is_crash_reboot else 3
    for attempt in range(_sd_max_attempts):
        wdt.feed()
        if mount_sd():
            sd_mounted = True
            break
        if attempt < _sd_max_attempts - 1:
            from logging.sdcard_mount import unmount as unmount_sd
            unmount_sd()
            time.sleep(1)
    wdt.feed()

    # Barometer (3 attempts)
    baro = None
    last_baro_err = None
    i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                  freq=config.I2C_FREQ, timeout=config.I2C_TIMEOUT_US)
    for attempt in range(3):
        wdt.feed()
        try:
            baro = BMP180(i2c, config.BMP180_ADDR)
            p, t = baro.read()
            break
        except Exception as e:
            last_baro_err = e
            baro = None
            if attempt < 2:
                wdt.feed()
                time.sleep_ms(500)

    # Power monitor (always init — cheap)
    power = PowerMonitor()
    v3, v5, v9 = power.read_all()

    if _is_crash_reboot:
        # ── CRASH FAST-REBOOT: skip preflight, go straight to logging ──
        blog("[FAST] SD={} Baro={}".format(
            'OK' if sd_mounted else 'FAIL',
            'OK' if baro else 'FAIL'))
        preflight_errors = []
        warnings = []
        manual_override = True  # treat as override
        if not sd_mounted or baro is None:
            blog("[FATAL] Hardware init failed on crash reboot")
            led.on()
            while True:
                wdt.feed()
                time.sleep(1)
    else:
        # ── NORMAL BOOT: full preflight checks ──────────
        blog("[2/7] LED started      fast blink = preflight running")

        step = 3
        preflight_errors = []

        if not sd_mounted:
            preflight_errors.append("SD card mount failed")
            blog(f"[{step}/7] SD card ...     FAIL — mount failed (3 attempts)")
        else:
            free_mb = free_space_mb()
            next_file = next_log_filename(config.LOG_FILENAME)
            blog(f"[{step}/7] SD card ...     OK — {free_mb:.0f} MB free → {next_file}")
        step += 1

        if baro is None:
            preflight_errors.append(f"Barometer: {last_baro_err}")
            blog(f"[{step}/7] Barometer ...  FAIL — {last_baro_err}")
        else:
            blog(f"[{step}/7] Barometer ...  OK — {p:.0f} Pa, {t:.1f}°C")
        step += 1

        warnings = power.check_health()
        if warnings:
            blog(f"[{step}/7] Power rails ... WARN")
            for w in warnings:
                blog(f"         {w}")
        else:
            blog(f"[{step}/7] Power rails ... OK — 3V3={v3}mV 5V={v5}mV 9V={v9}mV")
        step += 1

        # Check for manual override flag from TUI
        manual_override = False
        try:
            os.stat('_manual_override')
            manual_override = True
            os.remove('_manual_override')
            blog("  [OVERRIDE] Manual override active — skipping fatal halts")
        except OSError:
            pass

        # Handle preflight failures
        if preflight_errors:
            led.on()
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
            led.set_pattern([200, 200, 200, 1000])
            blog("  Proceeding despite errors.")
            blog("")

        # Critical failures
        sd_ok = "SD card" not in str(preflight_errors)
        if not sd_ok:
            blog("[FATAL] Cannot log without SD card. LED will stay solid.")
            led.on()
            while True:
                wdt.feed()
                time.sleep(1)

        if baro is None:
            blog("[FATAL] Cannot fly without barometer. LED will stay solid.")
            led.on()
            while True:
                wdt.feed()
                time.sleep(1)

    # ── Ground calibration (both paths) ────────────────
    ground_pressure = 101325.0
    if baro is not None:
        if not _is_crash_reboot:
            blog(f"[{step}/7] Calibrating ...", end="")
        pressure_sum = 0.0
        # Fewer samples on crash reboot for speed
        n_samples = 10 if _is_crash_reboot else config.GROUND_SAMPLES
        for i in range(n_samples):
            wdt.feed()
            p, t = baro.read()
            pressure_sum += p
            time.sleep_ms(20)
        ground_pressure = pressure_sum / n_samples
        if not _is_crash_reboot:
            blog(f" OK — ground P={ground_pressure:.0f} Pa")
            step += 1

    kalman = AltitudeKalman()
    fsm = FlightStateMachine()
    fsm.set_ground_reference(0.0)
    kalman.reset(0.0)

    # Open logger (creates per-flight folder)
    logger = None
    log_file = 'NONE'
    if sd_mounted:
        try:
            logger = FlightLogger(flush_every=config.LOG_FLUSH_EVERY,
                                  sync_every=config.LOG_SYNC_EVERY,
                                  wdt=wdt)
            log_file = logger.open()
            if not _is_crash_reboot:
                blog(f"[{step}/7] Logger open   {log_file}")
            else:
                blog(f"[FAST] Logger: {log_file}")
        except Exception as e:
            logger = None
            log_file = 'NONE'
            led.on()
            blog(f"Logger open FAIL — {e}")

    # Write crash report from previous session
    if _crash_data is not None and logger is not None:
        reason_names = {1: 'PWRON_RESET', 3: 'WDT_RESET', 5: 'SOFT_RESET'}
        logger.write_crash_report(_crash_data,
                                  reason_names.get(_reboot_reason, str(_reboot_reason)))

    # Write preflight metadata
    reboot_str = {1: 'PWRON', 3: 'WDT_CRASH', 5: 'SOFT'}.get(_reboot_reason, str(_reboot_reason))
    preflight_lines = [
        'UNSW Rocketry — MPR Altitude Logger',
        'Avionics v{}'.format(config.VERSION),
        'MicroPython {}'.format(sys.version),
        'Boot time: {} ms'.format(time.ticks_ms()),
        'Reboot reason: {}'.format(reboot_str),
        'Crash reboot: {}'.format('YES' if _is_crash_reboot else 'NO'),
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
    preflight_lines.append('Log format: v{} ({} byte frames)'.format(
        3, 40))
    if logger is not None:
        logger.write_preflight('\n'.join(preflight_lines))

    # ── All clear ─────────────────────────────────────
    if not _is_crash_reboot:
        blog("")
        if not preflight_errors:
            led.set_pattern(LED_PATTERNS[PAD])
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
    else:
        led.set_pattern(LED_PATTERNS[PAD])
        blog("[FAST] Logging at {} Hz — crash reboot complete\n".format(config.SAMPLE_RATE_HZ))

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

    # Diagnostic counters (wrap at 255 — u8 in frame)
    _i2c_errors = 0
    _overruns = 0
    _last_recover_ms = 0
    # Expensive diagnostics — updated once per second, reused across frames
    _free_kb = gc.mem_free() // 1024
    _cpu_temp_c = 67  # default ~27°C (offset +40)

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

    # Prime Kalman filter — run 20 readings through so it converges before
    # we start logging.  Prevents the initial altitude spike from noisy
    # first samples hitting the filter at high uncertainty.
    for _ in range(20):
        wdt.feed()
        p, _ = baro.read()
        alt = pressure_to_altitude(p, ground_pressure)
        kalman.update(alt, 1.0 / config.SAMPLE_RATE_HZ)

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

            # ── Diagnostics (cheap per-frame, expensive once per second) ──
            v3, v5, v9 = power.read_all()

            # ── Measure frame time ───────────────────
            frame_end_us = time.ticks_us()
            _frame_us = time.ticks_diff(frame_end_us, now_us)
            frame_us_sum += _frame_us
            if _frame_us > interval_us:
                _overruns = min(_overruns + 1, 255)

            # ── Log to SD ────────────────────────────
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
                    frame_us=min(_frame_us, 65535),
                    free_kb=_free_kb,
                    cpu_temp_c=_cpu_temp_c,
                    i2c_errors=_i2c_errors,
                    overruns=_overruns,
                )

            # ── Console output + slow diagnostics (1 Hz) ──
            loop_count += 1
            if time.ticks_diff(now_ms, last_print) >= 1000:
                hz = loop_count
                avg_frame_us = frame_us_sum // hz if hz else 0
                loop_count = 0
                frame_us_sum = 0
                last_print = now_ms

                # Expensive diagnostics — once per second, not every frame
                gc.collect()  # force GC so mem_free is meaningful
                _free_kb = gc.mem_free() // 1024
                _raw_temp = _temp_adc.read_u16()
                _cpu_temp_c = int(27 - (_raw_temp * 3.3 / 65535 - 0.706) / 0.001721 + 40) & 0xFF

                frames = logger.frames_written if logger is not None else 0
                _print_start = time.ticks_us()
                nb_print(
                    f"[{STATE_NAMES[state]:7s}] "
                    f"alt={alt_filt:7.1f}m  vel={vel_filt:+6.1f}m/s  "
                    f"P={pressure:.0f}Pa  T={temperature:.1f}°C  "
                    f"3V3={v3}mV  "
                    f"{hz}Hz {avg_frame_us}us  #{frames}"
                )
                # If print took >50ms, USB is blocking — disable console
                if _console_enabled and time.ticks_diff(time.ticks_us(), _print_start) > 50_000:
                    _console_enabled = False

            # ── SD recovery (try every ~5s if failed) ──
            if (logger is not None and logger.sd_failed
                    and time.ticks_diff(now_ms, _last_recover_ms) >= 5000):
                _last_recover_ms = now_ms
                logger.try_recover()

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
            _i2c_errors = min(_i2c_errors + 1, 255)
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
                    i2c_errors=_i2c_errors,
                    overruns=_overruns,
                )
            continue


# ── Entry point ───────────────────────────────────────────
core0_main()
