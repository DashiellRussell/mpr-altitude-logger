"""Minimal I2C diagnostic — run this to debug the BMP180 connection."""

import time
from machine import Pin, I2C, SoftI2C

SDA_PIN = 4
SCL_PIN = 5

print("\n=== I2C DEBUG ===\n")

# Test 1: Check pin states (are pull-ups present?)
sda = Pin(SDA_PIN, Pin.IN)
scl = Pin(SCL_PIN, Pin.IN)
print(f"Pin states (should both be 1 with pull-ups):")
print(f"  SDA (GP{SDA_PIN}): {sda.value()}")
print(f"  SCL (GP{SCL_PIN}): {scl.value()}")

if sda.value() == 0 or scl.value() == 0:
    print("  WARNING: Line held LOW — check wiring or stuck bus")

# Test 2: Hardware I2C scan
print(f"\nHardware I2C(0) scan on GP{SDA_PIN}/GP{SCL_PIN}:")
i2c_hw = I2C(0, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=100_000)
devs = i2c_hw.scan()
print(f"  Found: {['0x{:02X}'.format(d) for d in devs]}")

# Try to read chip ID if anything found
for addr in devs:
    try:
        chip_id = i2c_hw.readfrom_mem(addr, 0xD0, 1)[0]
        print(f"  0x{addr:02X} chip ID: 0x{chip_id:02X}")
    except OSError as e:
        print(f"  0x{addr:02X} readfrom_mem failed: {e}")

# Release hardware I2C before trying soft
del i2c_hw
time.sleep_ms(100)

# Test 3: SoftI2C scan
print(f"\nSoftI2C scan on GP{SDA_PIN}/GP{SCL_PIN}:")
i2c_soft = SoftI2C(sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=100_000)
devs = i2c_soft.scan()
print(f"  Found: {['0x{:02X}'.format(d) for d in devs]}")

for addr in devs:
    try:
        chip_id = i2c_soft.readfrom_mem(addr, 0xD0, 1)[0]
        print(f"  0x{addr:02X} chip ID: 0x{chip_id:02X}")
    except OSError as e:
        print(f"  0x{addr:02X} readfrom_mem failed: {e}")

del i2c_soft
time.sleep_ms(100)

# Test 4: Try every valid I2C0 pin pair
print("\nScanning ALL valid I2C0 pin pairs:")
pairs = [(0, 1), (4, 5), (8, 9), (12, 13), (16, 17), (20, 21)]
for sda_p, scl_p in pairs:
    try:
        bus = SoftI2C(sda=Pin(sda_p), scl=Pin(scl_p), freq=100_000)
        found = bus.scan()
        if found:
            print(f"  GP{sda_p}/GP{scl_p}: {['0x{:02X}'.format(d) for d in found]} ←←← FOUND!")
            for addr in found:
                try:
                    cid = bus.readfrom_mem(addr, 0xD0, 1)[0]
                    print(f"    chip ID: 0x{cid:02X}")
                except:
                    print(f"    chip ID read failed")
        else:
            print(f"  GP{sda_p}/GP{scl_p}: nothing")
        del bus
    except Exception as e:
        print(f"  GP{sda_p}/GP{scl_p}: error — {e}")
    time.sleep_ms(50)

print("\n=== DONE ===")
