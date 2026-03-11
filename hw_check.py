"""
hw_check.py — First-boot hardware sanity check.

This is the FIRST thing you flash onto the Pico when you get the
avionics board assembled. It tests each component one at a time,
blinking the LED and printing results over USB serial.

Upload this single file to the Pico as main.py. Connect via serial
monitor (Thonny, PuTTY, screen, etc.) at 115200 baud.

No dependencies on the rest of the avionics codebase — this is
standalone so you can isolate hardware issues before loading the
full MPR Altitude Logger.

Wiring expected (edit pins below if different):
    BMP180:  SDA=GP4, SCL=GP5 (I2C0) — GY-68 breakout
    SD Card: MISO=GP16, MOSI=GP19, SCK=GP18, CS=GP17 (SPI0)
    V_3V3:   GP28 (ADC2) direct — 3.3V rail
    V_5V:    GP26 (ADC0) through 1k/1k voltage divider
    V_9V:    GP27 (ADC1) through 2k/1k voltage divider
    LED:     GP25 (onboard)
"""

import time
import struct
import os
from machine import Pin, I2C, SoftI2C, SPI, ADC, PWM, freq

# ═══════════════════════════════════════════════════════════════
#  EDIT THESE TO MATCH YOUR BOARD
# ═══════════════════════════════════════════════════════════════

I2C_SDA = 4           # GP4 — ALT-DTA
I2C_SCL = 5           # GP5 — ALT-CLK
I2C_FREQ = 100_000    # 100kHz — conservative, bump to 400k once working
BMP_ADDR = 0x77       # GY-68 default

SPI_ID = 0
SPI_SCK = 18          # GP18 — SD-CLK
SPI_MOSI = 19         # GP19 — SD-SlaveIn
SPI_MISO = 16         # GP16 — SD-SlaveOut
SPI_CS = 17           # GP17 — SD-ChipSelect

ADC_3V = 28           # GP28 (A2) — 3.3V rail (direct, no divider)
ADC_5V = 26           # GP26 (A0) — 5V rail
ADC_9V = 27           # GP27 (A1) — 9V rail
VDIV_3V = 1.0         # direct — 3.3V within ADC range
VDIV_5V = 2.0         # voltage divider — see schematic
VDIV_9V = 3.0         # 2k/1k divider

LED_PIN = 25

# ═══════════════════════════════════════════════════════════════


def blink(led, n=3, on_ms=100, off_ms=100):
    for _ in range(n):
        led.on()
        time.sleep_ms(on_ms)
        led.off()
        time.sleep_ms(off_ms)


def header(title):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def test_led(led):
    header("TEST 1: Onboard LED")
    print("  Blinking 5 times...")
    blink(led, 5, 200, 200)
    print("  [OK] If you saw the LED blink, it works.")
    return True


def test_i2c():
    header("TEST 2: I2C Bus Scan")
    try:
        i2c = SoftI2C(sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=I2C_FREQ)
        devices = i2c.scan()

        if not devices:
            print("  [FAIL] No I2C devices found!")
            print("  Check: SDA/SCL wiring, pull-up resistors, power")
            return False, None

        print(f"  Found {len(devices)} device(s):")
        for addr in devices:
            label = ""
            if addr in (0x76, 0x77):
                label = " ← BMP180"
            elif addr in (0x68, 0x69):
                label = " ← MPU6050/ICM"
            elif addr == 0x1E:
                label = " ← HMC5883L"
            elif addr == 0x3C:
                label = " ← SSD1306 OLED"
            print(f"    0x{addr:02X}{label}")

        return True, i2c
    except Exception as e:
        print(f"  [FAIL] I2C init error: {e}")
        return False, None


def test_barometer(i2c):
    header("TEST 3: BMP180 Barometer")
    if i2c is None:
        print("  [SKIP] No I2C bus available")
        return False

    try:
        time.sleep_ms(50)

        # Check chip ID
        chip_id = i2c.readfrom_mem(BMP_ADDR, 0xD0, 1)[0]
        print(f"  Chip ID: 0x{chip_id:02X} (expected 0x55 for BMP180)")

        if chip_id != 0x55:
            print(f"  [FAIL] Unexpected chip ID. Expected BMP180 (0x55)")
            return False

        # Soft reset
        i2c.writeto_mem(BMP_ADDR, 0xE0, bytes([0xB6]))
        time.sleep_ms(10)

        # Read calibration data (BMP180: 22 bytes at 0xAA, big-endian)
        cal = i2c.readfrom_mem(BMP_ADDR, 0xAA, 22)
        AC1 = struct.unpack_from('>h', cal, 0)[0]
        AC5 = struct.unpack_from('>H', cal, 8)[0]
        AC6 = struct.unpack_from('>H', cal, 10)[0]
        MC = struct.unpack_from('>h', cal, 18)[0]
        MD = struct.unpack_from('>h', cal, 20)[0]
        print(f"  Calibration AC1: {AC1} (should be non-zero)")
        if AC1 == 0 or AC1 == -1:
            print("  [WARN] Calibration data looks wrong")

        # Read temperature
        i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0x2E]))
        time.sleep_ms(5)
        raw = i2c.readfrom_mem(BMP_ADDR, 0xF6, 2)
        UT = struct.unpack_from('>H', raw, 0)[0]

        X1 = (UT - AC6) * AC5 // 32768
        X2 = (MC * 2048) // (X1 + MD)
        B5 = X1 + X2
        temp_c = (B5 + 8) / 160.0

        # Read pressure (oss=3, ultra high res)
        i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0xF4]))
        time.sleep_ms(26)
        raw = i2c.readfrom_mem(BMP_ADDR, 0xF6, 3)
        raw_press = ((raw[0] << 16) | (raw[1] << 8) | raw[2]) >> 5

        print(f"  Raw temp:  {UT}")
        print(f"  Raw press: {raw_press}")
        print(f"  Temperature: {temp_c:.1f} °C")

        if -40 < temp_c < 85:
            print("  [OK] Temperature in valid range")
        else:
            print(f"  [WARN] Temperature {temp_c:.1f}°C seems off")

        # Read 10 pressure samples for noise check
        pressures = []
        for _ in range(10):
            i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0xF4]))
            time.sleep_ms(26)
            raw = i2c.readfrom_mem(BMP_ADDR, 0xF6, 3)
            p = ((raw[0] << 16) | (raw[1] << 8) | raw[2]) >> 5
            pressures.append(p)

        noise = max(pressures) - min(pressures)
        print(f"  Pressure noise (10 samples): {noise} LSB")
        if noise < 200:
            print("  [OK] Noise level acceptable")
        else:
            print("  [WARN] High noise — check wiring, decoupling cap")

        return True

    except OSError as e:
        print(f"  [FAIL] Cannot communicate with BMP180 at 0x{BMP_ADDR:02X}")
        print(f"  Error: {e}")
        return False


def test_adc():
    header("TEST 4: ADC Voltage Readings")
    ok = True

    for name, pin_num, divider, lo, hi in [
        ("3.3V", ADC_3V,   VDIV_3V, 3.0, 3.6),
        ("5V",   ADC_5V,   VDIV_5V, 3.0, 7.0),
        ("9V",   ADC_9V,   VDIV_9V, 5.0, 12.0),
    ]:
        try:
            adc = ADC(Pin(pin_num))
            raw = adc.read_u16()
            v_adc = (raw / 65535) * 3.3
            v_actual = v_adc * divider

            status = "[OK]"
            if v_actual < lo or v_actual > hi:
                status = "[WARN] Out of range"

            print(f"  {name:5s} (GP{pin_num}): raw={raw:5d}  adc={v_adc:.3f}V  actual={v_actual:.2f}V  {status}")

        except Exception as e:
            print(f"  {name:5s} (GP{pin_num}): [FAIL] {e}")
            ok = False

    if not ok:
        print("  Note: readings will be 0 or wrong if voltage dividers aren't connected")

    return ok


def test_sd_card():
    header("TEST 5: SD Card (SPI)")
    try:
        spi = SPI(SPI_ID, baudrate=1_000_000, polarity=0, phase=0,
                  sck=Pin(SPI_SCK), mosi=Pin(SPI_MOSI), miso=Pin(SPI_MISO))
        cs = Pin(SPI_CS, Pin.OUT, value=1)

        # Try to import sdcard driver
        try:
            import sdcard
        except ImportError:
            print("  [FAIL] sdcard module not found!")
            print("  You need to install the MicroPython sdcard driver.")
            print("  Download from: github.com/micropython/micropython-lib")
            print("  Copy sdcard.py to the Pico's filesystem.")
            return False

        sd = sdcard.SDCard(spi, cs)
        vfs = os.VfsFat(sd)
        os.mount(vfs, "/sd")

        # Check free space
        stat = os.statvfs("/sd")
        total_mb = (stat[0] * stat[2]) / (1024 * 1024)
        free_mb = (stat[0] * stat[3]) / (1024 * 1024)

        print(f"  Total: {total_mb:.0f} MB")
        print(f"  Free:  {free_mb:.0f} MB")

        # Test write
        test_file = "/sd/_hw_check_test.tmp"
        test_data = b"AVIONICS_HW_CHECK_OK\n"

        with open(test_file, 'wb') as f:
            f.write(test_data)

        with open(test_file, 'rb') as f:
            readback = f.read()

        os.remove(test_file)

        if readback == test_data:
            print("  Write/read test: [OK]")
        else:
            print("  Write/read test: [FAIL] Data mismatch!")
            return False

        # List existing flight logs
        files = os.listdir("/sd")
        logs = [f for f in files if f.endswith('.bin')]
        if logs:
            print(f"  Existing flight logs: {logs}")

        os.umount("/sd")
        print("  [OK] SD card working")
        return True

    except Exception as e:
        print(f"  [FAIL] SD card error: {e}")
        print("  Check: SPI wiring, CS pin, card inserted, card formatted FAT32")
        return False


def test_timing():
    header("TEST 6: Loop Timing")
    try:
        i2c = SoftI2C(sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=I2C_FREQ)

        # Verify BMP180 is responsive
        try:
            i2c.readfrom_mem(BMP_ADDR, 0xD0, 1)
        except:
            print("  [SKIP] Barometer not available for timing test")
            return True

        # Time 100 BMP180 temp+pressure read cycles
        times = []
        for _ in range(100):
            t0 = time.ticks_us()
            # Trigger temp read + wait + read result
            i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0x2E]))
            time.sleep_ms(5)
            i2c.readfrom_mem(BMP_ADDR, 0xF6, 2)
            # Trigger pressure read (oss=3) + wait + read result
            i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0xF4]))
            time.sleep_ms(26)
            i2c.readfrom_mem(BMP_ADDR, 0xF6, 3)
            t1 = time.ticks_us()
            times.append(time.ticks_diff(t1, t0))

        avg = sum(times) // len(times)
        worst = max(times)
        best = min(times)

        target_hz = 25
        budget_us = 1_000_000 // target_hz

        print(f"  I2C read (100 samples):")
        print(f"    Best:  {best} µs")
        print(f"    Avg:   {avg} µs")
        print(f"    Worst: {worst} µs")
        print(f"  Loop budget at {target_hz} Hz: {budget_us} µs")
        print(f"  Headroom: {budget_us - worst} µs")

        if worst < budget_us:
            print(f"  [OK] Plenty of time for {target_hz} Hz")
        else:
            print(f"  [WARN] Tight timing — consider reducing sample rate")

        return True

    except Exception as e:
        print(f"  [FAIL] Timing test error: {e}")
        return False


def run():
    led = Pin(LED_PIN, Pin.OUT)

    print("\n" + "═" * 50)
    print("  MPR ALTITUDE LOGGER — HARDWARE CHECK")
    print(f"  CPU: RP2040 @ {freq() // 1_000_000} MHz")
    print("═" * 50)

    results = {}

    results["LED"] = test_led(led)
    i2c_ok, i2c = test_i2c()
    results["I2C"] = i2c_ok
    results["Barometer"] = test_barometer(i2c)
    results["ADC"] = test_adc()
    results["SD Card"] = test_sd_card()
    results["Timing"] = test_timing()

    # Summary
    print("\n" + "═" * 50)
    print("  RESULTS")
    print("═" * 50)

    passed = 0
    failed = 0
    for name, ok in results.items():
        status = "PASS ✓" if ok else "FAIL ✗"
        print(f"  {name:15s} {status}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  {passed}/{passed + failed} tests passed")

    if failed == 0:
        print("\n  ★ ALL CLEAR — ready to load MPR Altitude Logger! ★")
        blink(led, 10, 50, 50)
    else:
        print(f"\n  ⚠ {failed} ISSUE(S) — fix before loading MPR Altitude Logger")
        blink(led, 3, 500, 500)

    print("═" * 50 + "\n")


# Auto-run on boot
run()
