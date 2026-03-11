"""
Ground test script — run this on the pad before every flight.
Checks all hardware, logs a few seconds of data, reports status.

Upload to Pico and run via: import ground_test
"""

import time
from machine import I2C, Pin, freq
import config


def run():
    print("\n" + "="*50)
    print("  GROUND TEST — Hardware Verification")
    print("="*50 + "\n")

    errors = 0

    # ── CPU ────────────────────────────────────
    freq(200_000_000)
    print(f"[OK] CPU: {freq() // 1_000_000} MHz")

    # ── SD Card ────────────────────────────────
    print("\n--- SD Card ---")
    try:
        from logging.sdcard_mount import mount, free_space_mb, is_mounted
        if mount():
            mb = free_space_mb()
            print(f"[OK] SD mounted — {mb:.0f} MB free")
        else:
            print("[FAIL] SD mount failed")
            errors += 1
    except Exception as e:
        print(f"[FAIL] SD error: {e}")
        errors += 1

    # ── Barometer ──────────────────────────────
    print("\n--- Barometer ---")
    try:
        from sensors.barometer import BMP180, pressure_to_altitude
        i2c = I2C(config.I2C_ID, sda=Pin(config.I2C_SDA),
                   scl=Pin(config.I2C_SCL), freq=config.I2C_FREQ)
        
        devices = i2c.scan()
        print(f"  I2C devices: {['0x{:02x}'.format(d) for d in devices]}")
        
        baro = BMP180(i2c, config.BMP180_ADDR)
        
        # Read 10 samples
        pressures = []
        temps = []
        for _ in range(10):
            p, t = baro.read()
            pressures.append(p)
            temps.append(t)
            time.sleep_ms(50)
        
        avg_p = sum(pressures) / len(pressures)
        avg_t = sum(temps) / len(temps)
        p_noise = max(pressures) - min(pressures)
        
        print(f"[OK] Pressure: {avg_p:.1f} Pa (noise: {p_noise:.1f} Pa)")
        print(f"[OK] Temperature: {avg_t:.1f} °C")
        print(f"     Expected sea-level: ~101325 Pa")
        
        if avg_p < 80000 or avg_p > 110000:
            print("[WARN] Pressure out of expected range")
    except Exception as e:
        print(f"[FAIL] Barometer error: {e}")
        errors += 1

    # ── Power Rails ────────────────────────────
    print("\n--- Power Rails ---")
    try:
        from sensors.power import PowerMonitor
        pwr = PowerMonitor()
        batt, v5, v9 = pwr.read_all()
        
        print(f"  Battery: {batt} mV {'[OK]' if batt > 3000 else '[LOW]'}")
        print(f"  5V rail: {v5} mV {'[OK]' if 4500 < v5 < 5500 else '[WARN]'}")
        print(f"  9V rail: {v9} mV {'[OK]' if 8000 < v9 < 10000 else '[WARN]'}")
        
        warnings = pwr.check_health()
        for w in warnings:
            print(f"[WARN] {w}")
            errors += 1
    except Exception as e:
        print(f"[FAIL] Power monitor error: {e}")
        errors += 1

    # ── ARM Switch ─────────────────────────────
    print("\n--- ARM Switch ---")
    try:
        from utils.hardware import ArmSwitch
        arm = ArmSwitch()
        print(f"  State: {'ARMED' if arm.armed else 'SAFE'}")
        if arm.armed:
            print("[WARN] Board is ARMED — disarm before handling!")
    except Exception as e:
        print(f"[FAIL] ARM switch error: {e}")
        errors += 1

    # ── Deploy Pin ─────────────────────────────
    print("\n--- Deploy Channel ---")
    deploy_pin = Pin(config.DEPLOY_PIN, Pin.IN)
    print(f"  Pin state: {'HIGH [WARN]' if deploy_pin.value() else 'LOW [OK]'}")

    # ── LED + Buzzer ───────────────────────────
    print("\n--- Indicators ---")
    try:
        led = Pin(config.LED_PIN, Pin.OUT)
        led.on()
        time.sleep_ms(200)
        led.off()
        print("[OK] LED blinked")
    except:
        print("[FAIL] LED")
        errors += 1

    try:
        from utils.hardware import Buzzer
        bz = Buzzer()
        bz.beep(2700, 200)
        time.sleep_ms(300)
        bz.off()
        print("[OK] Buzzer beeped")
    except:
        print("[FAIL] Buzzer")
        errors += 1

    # ── Timing test ────────────────────────────
    print("\n--- Loop Timing (100 cycles) ---")
    if 'baro' in dir():
        times = []
        for _ in range(100):
            t0 = time.ticks_us()
            p, t = baro.read()
            t1 = time.ticks_us()
            times.append(time.ticks_diff(t1, t0))
        
        avg_us = sum(times) // len(times)
        max_us = max(times)
        target_us = 1_000_000 // config.SAMPLE_RATE_HZ
        
        print(f"  Baro read: avg={avg_us}µs, max={max_us}µs")
        print(f"  Target loop: {target_us}µs ({config.SAMPLE_RATE_HZ} Hz)")
        print(f"  Headroom: {target_us - avg_us}µs {'[OK]' if avg_us < target_us else '[TIGHT]'}")

    # ── Summary ────────────────────────────────
    print("\n" + "="*50)
    if errors == 0:
        print("  ALL CHECKS PASSED — READY FOR FLIGHT")
    else:
        print(f"  {errors} ISSUE(S) FOUND — FIX BEFORE FLIGHT")
    print("="*50 + "\n")

    return errors


if __name__ == "__main__":
    run()
