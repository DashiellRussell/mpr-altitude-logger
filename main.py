"""
main.py — MPR Altitude Logger entry point.

DUAL-CORE ARCHITECTURE:
    Core 0: Sensor reads → Kalman filter → State machine → SD log
    Core 1: Deployment pulse management, buzzer, LED patterns

Core 0 does the time-critical work in a tight loop at 25 Hz.
Core 1 handles slower I/O that can't block the sensor loop.

Boot sequence:
    1. Mount SD card
    2. Init sensors + hardware
    3. Calibrate ground pressure (average N samples)
    4. Enter main loop (waiting for launch)
    5. Auto-detect launch → track flight → deploy → recovery beacon
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
from logging.datalog import FlightLogger
from logging.sdcard_mount import mount as mount_sd, free_space_mb
from utils.hardware import (
    StatusLED, Buzzer, DeployChannel, ArmSwitch, LED_PATTERNS
)


# ── Shared state between cores (keep minimal) ───────────────
# Using simple globals — MicroPython's _thread is cooperative enough
# that atomic-ish reads of these are fine for our purposes.

_current_state = PAD
_deploy_drogue_flag = False
_deploy_main_flag = False
_landed = False


def core1_task():
    """
    Core 1: handles deployment pulses, LED, buzzer.
    
    Runs in a slower loop (~20 Hz) since these are not time-critical.
    Reads shared flags set by Core 0's state machine.
    """
    global _deploy_drogue_flag, _deploy_main_flag, _landed, _current_state

    led = StatusLED()
    buzzer = Buzzer()
    deploy = DeployChannel()
    arm_switch = ArmSwitch()

    deploy.safe()  # ensure safe on boot
    led.set_pattern([1000, 1000])  # slow blink = booting

    # Startup beeps
    for _ in range(3):
        buzzer.beep(2700, 100)
        time.sleep_ms(200)

    last_state = -1

    while True:
        now = time.ticks_ms()

        # Update LED pattern on state change
        if _current_state != last_state:
            last_state = _current_state
            pattern = LED_PATTERNS.get(_current_state)
            if pattern is None:
                led.on()  # solid = landed
            else:
                led.set_pattern(pattern)

        # Handle deployment fires (one-shot)
        if _deploy_drogue_flag and arm_switch.armed:
            deploy.fire()
            buzzer.beep(1500, 500)
            _deploy_drogue_flag = False

        if _deploy_main_flag and arm_switch.armed:
            deploy.fire()
            buzzer.beep(1000, 500)
            _deploy_main_flag = False

        # Recovery beacon when landed
        if _landed:
            buzzer.recovery_beacon(now)

        # Tick all hardware
        led.tick(now)
        buzzer.tick(now)
        deploy.tick(now)

        time.sleep_ms(50)  # ~20 Hz


def core0_main():
    """
    Core 0: sensor loop → filter → state machine → logger.
    
    This is the time-critical path. Target: 25 Hz with minimal jitter.
    """
    global _current_state, _deploy_drogue_flag, _deploy_main_flag, _landed

    print("\n╔══════════════════════════════════════════╗")
    print("║   UNSW ROCKETRY — MPR ALTITUDE LOGGER    ║")
    print("╚══════════════════════════════════════════╝\n")

    # ── Overclock for headroom ────────────────────────
    freq(200_000_000)  # 200 MHz (stock is 125 MHz)
    print(f"[BOOT] CPU freq: {freq() // 1_000_000} MHz")

    # ── Mount SD card ─────────────────────────────────
    print("[BOOT] Mounting SD card...")
    if not mount_sd():
        print("[FATAL] SD card mount failed. Halting.")
        _error_halt()
        return

    free_mb = free_space_mb()
    print(f"[BOOT] SD card OK — {free_mb:.0f} MB free")

    # ── Init sensors ──────────────────────────────────
    print("[BOOT] Init barometer...")
    i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                  freq=config.I2C_FREQ)
    baro = BMP180(i2c, config.BMP180_ADDR)

    print("[BOOT] Init power monitor...")
    power = PowerMonitor()
    warnings = power.check_health()
    for w in warnings:
        print(f"[WARN] {w}")

    print("[BOOT] Init arm switch...")
    arm_switch = ArmSwitch()

    # ── Init altitude logger ──────────────────────────
    kalman = AltitudeKalman()
    fsm = FlightStateMachine()

    # ── Ground calibration ────────────────────────────
    print(f"[CAL] Averaging {config.GROUND_SAMPLES} pressure samples...")
    pressure_sum = 0.0
    for i in range(config.GROUND_SAMPLES):
        p, t = baro.read()
        pressure_sum += p
        time.sleep_ms(20)
    ground_pressure = pressure_sum / config.GROUND_SAMPLES
    ground_alt = pressure_to_altitude(ground_pressure, ground_pressure)  # = 0

    print(f"[CAL] Ground pressure: {ground_pressure:.1f} Pa")
    fsm.set_ground_reference(0.0)
    kalman.reset(0.0)

    # ── Open logger ───────────────────────────────────
    logger = FlightLogger(config.LOG_FILENAME, flush_every=config.LOG_FLUSH_EVERY)
    log_file = logger.open()
    print(f"[LOG] Logging to: {log_file}")

    # ── Launch Core 1 ─────────────────────────────────
    print("[BOOT] Starting Core 1 (hardware I/O)...")
    _thread.start_new_thread(core1_task, ())

    # ── Status ────────────────────────────────────────
    v3_mv, v5_mv, v9_mv = power.read_all()
    print(f"[PWR] 3V3={v3_mv}mV  5V={v5_mv}mV  9V={v9_mv}mV")
    print(f"[ARM] {'ARMED' if arm_switch.armed else 'DISARMED'}")
    print(f"\n[RDY] MPR Altitude Logger ready — {config.SAMPLE_RATE_HZ} Hz loop")
    print("[RDY] Waiting for launch...\n")

    # ── Main sensor loop ──────────────────────────────
    interval_us = 1_000_000 // config.SAMPLE_RATE_HZ
    last_time = time.ticks_us()
    last_print = time.ticks_ms()
    loop_count = 0

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
        fsm.set_armed(arm_switch.armed)
        state, deploy_drogue, deploy_main = fsm.update(alt_filt, vel_filt, now_ms)

        # Set shared flags for Core 1
        _current_state = state
        if deploy_drogue:
            _deploy_drogue_flag = True
        if deploy_main:
            _deploy_main_flag = True
        if state == LANDED:
            _landed = True

        # ── Build flags byte ──────────────────────
        flags = 0
        if arm_switch.armed:
            flags |= 0x01
        if fsm.drogue_fired:
            flags |= 0x02
        if fsm.main_fired:
            flags |= 0x04

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

        # ── Console output (1 Hz, PAD state only for debug) ──
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
                f"{'ARM' if arm_switch.armed else 'SAFE'}  "
                f"{hz}Hz  #{logger.frames_written}"
            )

        # ── Post-landing shutdown ─────────────────
        if state == LANDED and time.ticks_diff(now_ms, fsm.apogee_time) > 120_000:
            # 2 min after landing, flush + print stats
            stats = fsm.get_stats()
            print(f"\n[LANDED] Flight complete!")
            print(f"  Max altitude: {stats['max_alt_m']:.1f} m AGL")
            print(f"  Max velocity: {stats['max_vel_ms']:.1f} m/s")
            print(f"  Frames logged: {logger.frames_written}")
            print(f"  Drogue fired: {stats['drogue_fired']}")
            print(f"  Main fired: {stats['main_fired']}")
            logger.close()
            print("[LOG] File closed. Safe to remove SD card.")
            # Don't return — keep Core 1 buzzer running for recovery
            while True:
                time.sleep(1)


def _error_halt():
    """Flash LED rapidly on fatal error."""
    led = Pin(config.LED_PIN, Pin.OUT)
    while True:
        led.toggle()
        time.sleep_ms(100)


# ── Entry point ───────────────────────────────────────────
core0_main()
