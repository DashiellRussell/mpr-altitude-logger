/**
 * MicroPython code strings sent to the Pico via raw REPL.
 *
 * These are NOT TypeScript — they are literal MicroPython source that
 * runs on the RP2040. Ported directly from tools/preflight.py.
 */

/** Read system info: MicroPython version, CPU frequency, free memory, avionics version */
export const SYSINFO_CODE = `\
import sys, gc, machine
gc.collect()
try:
    import config
    av = config.VERSION
except:
    av = '?'
print('{},{},{},{}'.format(sys.version, machine.freq(), gc.mem_free(), av))
`;

/** Scan I2C bus for connected devices */
export const I2C_SCAN_CODE = `\
from machine import SoftI2C, Pin
i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
devs = i2c.scan()
print(','.join(str(d) for d in devs))
`;

/** Read BMP180 chip ID register to verify barometer presence */
export const BARO_CHECK_CODE = `\
from machine import SoftI2C, Pin
i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
cid = i2c.readfrom_mem(0x77, 0xD0, 1)[0]
print(cid)
`;

/** Mount SD card, check capacity, do write/read test */
export const SD_CHECK_CODE = `\
import os
from machine import SPI, Pin
import sdcard
import time
try:
    os.umount('/sd')
except:
    pass
cs = Pin(17, Pin.OUT)
cs.value(1)
time.sleep_ms(100)
spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs.value(1)
spi.write(b'\\xff' * 10)
time.sleep_ms(10)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
st = os.statvfs('/sd')
total = (st[0] * st[2]) // (1024*1024)
free = (st[0] * st[3]) // (1024*1024)
with open('/sd/_test.tmp', 'wb') as f:
    f.write(b'OK')
with open('/sd/_test.tmp', 'rb') as f:
    d = f.read()
os.remove('/sd/_test.tmp')
os.umount('/sd')
print('{},{},{}'.format(total, free, d == b'OK'))
`;

/** Read all three ADC channels for voltage rail check */
export const ADC_CHECK_CODE = `\
from machine import ADC, Pin
a3 = ADC(Pin(28)).read_u16()
a5 = ADC(Pin(26)).read_u16()
a9 = ADC(Pin(27)).read_u16()
print('{},{},{}'.format(a3, a5, a9))
`;

/** Toggle onboard LED on/off to verify it works */
export const LED_CHECK_CODE = `\
from machine import Pin
import time
try:
    led = Pin(25, Pin.OUT)
    led.on()
    time.sleep_ms(300)
    led.off()
    time.sleep_ms(200)
    led.on()
    time.sleep_ms(300)
    led.off()
    print('OK')
except Exception as e:
    print('ERR:{}'.format(e))
`;

/**
 * BMP180 init code: sets up I2C, reads calibration data, defines _poll().
 * After executing this, call _poll() to get a single sensor reading.
 *
 * Note: uses raw string to preserve \x escape sequences for MicroPython.
 */
export const INIT_CODE = String.raw`
import struct, time
from machine import SoftI2C, Pin, ADC

_i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
_addr = 0x77

_cal = _i2c.readfrom_mem(_addr, 0xAA, 22)
_AC1 = struct.unpack_from('>h', _cal, 0)[0]
_AC2 = struct.unpack_from('>h', _cal, 2)[0]
_AC3 = struct.unpack_from('>h', _cal, 4)[0]
_AC4 = struct.unpack_from('>H', _cal, 6)[0]
_AC5 = struct.unpack_from('>H', _cal, 8)[0]
_AC6 = struct.unpack_from('>H', _cal, 10)[0]
_B1 = struct.unpack_from('>h', _cal, 12)[0]
_B2 = struct.unpack_from('>h', _cal, 14)[0]
_MB = struct.unpack_from('>h', _cal, 16)[0]
_MC = struct.unpack_from('>h', _cal, 18)[0]
_MD = struct.unpack_from('>h', _cal, 20)[0]

_a3v = ADC(Pin(28))
_a5v = ADC(Pin(26))
_a9v = ADC(Pin(27))

def _poll():
    _i2c.writeto_mem(_addr, 0xF4, b'\x2e')
    time.sleep_ms(5)
    r = _i2c.readfrom_mem(_addr, 0xF6, 2)
    UT = (r[0] << 8) | r[1]
    X1 = (UT - _AC6) * _AC5 // 32768
    X2 = (_MC * 2048) // (X1 + _MD)
    B5 = X1 + X2
    t = (B5 + 8) / 160.0
    _i2c.writeto_mem(_addr, 0xF4, b'\xf4')
    time.sleep_ms(26)
    r = _i2c.readfrom_mem(_addr, 0xF6, 3)
    UP = ((r[0] << 16) | (r[1] << 8) | r[2]) >> 5
    B6 = B5 - 4000
    X1 = (_B2 * (B6 * B6 // 4096)) // 2048
    X2 = _AC2 * B6 // 2048
    X3 = X1 + X2
    B3 = (((_AC1 * 4 + X3) << 3) + 2) // 4
    X1 = _AC3 * B6 // 8192
    X2 = (_B1 * (B6 * B6 // 4096)) // 65536
    X3 = (X1 + X2 + 2) // 4
    B4 = _AC4 * (X3 + 32768) // 65536
    B7 = (UP - B3) * (50000 >> 3)
    if B7 < 0x80000000:
        p = (B7 * 2) // B4
    else:
        p = (B7 // B4) * 2
    X1 = (p // 256) * (p // 256)
    X1 = (X1 * 3038) // 65536
    X2 = (-7357 * p) // 65536
    p = p + (X1 + X2 + 3791) // 16
    v3 = _a3v.read_u16()
    v5 = _a5v.read_u16()
    v9 = _a9v.read_u16()
    print('{},{:.1f},{},{},{}'.format(p, t, v3, v5, v9))
`;

/**
 * Ground pressure calibration: averages 10 pressure readings on-Pico
 * in a single round-trip instead of 20 serial exchanges.
 *
 * Uses double-backslash escapes because this goes through JS string
 * then raw REPL — the \\x2e and \\xf4 need to become \x2e and \xf4
 * when they hit MicroPython.
 */
export const CALIBRATE_CODE = `
_ps = []
for _ in range(10):
    _i2c.writeto_mem(_addr, 0xF4, b'\\x2e')
    time.sleep_ms(5)
    r = _i2c.readfrom_mem(_addr, 0xF6, 2)
    UT = (r[0] << 8) | r[1]
    X1 = (UT - _AC6) * _AC5 // 32768
    X2 = (_MC * 2048) // (X1 + _MD)
    B5 = X1 + X2
    _i2c.writeto_mem(_addr, 0xF4, b'\\xf4')
    time.sleep_ms(26)
    r = _i2c.readfrom_mem(_addr, 0xF6, 3)
    UP = ((r[0] << 16) | (r[1] << 8) | r[2]) >> 5
    B6 = B5 - 4000
    X1 = (_B2 * (B6 * B6 // 4096)) // 2048
    X2 = _AC2 * B6 // 2048
    X3 = X1 + X2
    B3 = (((_AC1 * 4 + X3) << 3) + 2) // 4
    X1 = _AC3 * B6 // 8192
    X2 = (_B1 * (B6 * B6 // 4096)) // 65536
    X3 = (X1 + X2 + 2) // 4
    B4 = _AC4 * (X3 + 32768) // 65536
    B7 = (UP - B3) * (50000 >> 3)
    if B7 < 0x80000000:
        p = (B7 * 2) // B4
    else:
        p = (B7 // B4) * 2
    X1 = (p // 256) * (p // 256)
    X1 = (X1 * 3038) // 65536
    X2 = (-7357 * p) // 65536
    p = p + (X1 + X2 + 3791) // 16
    _ps.append(p)
print(sum(_ps) // len(_ps))
`;

/** Single poll command — call after INIT_CODE has been executed */
export const POLL_CMD = '_poll()';

// ══════════════════════════════════════════════════════════════
//  DETAILED CHECK COMMANDS — multi-step, returns PASS/FAIL per sub-check
//  Output format: "SUBCHECK_NAME:PASS:detail" or "SUBCHECK_NAME:FAIL:detail"
// ══════════════════════════════════════════════════════════════

/** Detailed I2C check: bus init, device scan, BMP address */
export const I2C_DETAIL_CODE = `\
from machine import SoftI2C, Pin
try:
    i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
    print('Bus Init:PASS:SDA=GP4 SCL=GP5 100kHz')
except Exception as e:
    print('Bus Init:FAIL:{}'.format(e))
    raise SystemExit
devs = i2c.scan()
if len(devs) > 0:
    hexl = ' '.join('0x{:02X}'.format(d) for d in devs)
    print('Device Scan:PASS:Found {} device(s): {}'.format(len(devs), hexl))
else:
    print('Device Scan:FAIL:No devices found on bus')
if 0x77 in devs:
    print('BMP180 Addr:PASS:0x77 present')
elif 0x76 in devs:
    print('BMP180 Addr:FAIL:0x76 found (BMP280?) expected 0x77')
else:
    print('BMP180 Addr:FAIL:0x77 not found')
`;

/** Detailed barometer check: chip ID, calibration, temp reading, pressure noise */
export const BARO_DETAIL_CODE = `\
import struct, time
from machine import SoftI2C, Pin
i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
try:
    cid = i2c.readfrom_mem(0x77, 0xD0, 1)[0]
    if cid == 0x55:
        print('Chip ID:PASS:0x{:02X} (BMP180)'.format(cid))
    else:
        print('Chip ID:FAIL:0x{:02X} expected 0x55'.format(cid))
except Exception as e:
    print('Chip ID:FAIL:{}'.format(e))
try:
    i2c.writeto_mem(0x77, 0xE0, bytes([0xB6]))
    time.sleep_ms(10)
    print('Soft Reset:PASS:Reset command sent')
except Exception as e:
    print('Soft Reset:FAIL:{}'.format(e))
try:
    cal = i2c.readfrom_mem(0x77, 0xAA, 22)
    AC1 = struct.unpack_from('>h', cal, 0)[0]
    AC5 = struct.unpack_from('>H', cal, 8)[0]
    AC6 = struct.unpack_from('>H', cal, 10)[0]
    MC = struct.unpack_from('>h', cal, 18)[0]
    MD = struct.unpack_from('>h', cal, 20)[0]
    if AC1 != 0 and AC1 != -1:
        print('Calibration:PASS:AC1={} AC5={} AC6={}'.format(AC1, AC5, AC6))
    else:
        print('Calibration:FAIL:AC1={} looks wrong'.format(AC1))
except Exception as e:
    print('Calibration:FAIL:{}'.format(e))
try:
    i2c.writeto_mem(0x77, 0xF4, bytes([0x2E]))
    time.sleep_ms(5)
    raw = i2c.readfrom_mem(0x77, 0xF6, 2)
    UT = struct.unpack_from('>H', raw, 0)[0]
    X1 = (UT - AC6) * AC5 // 32768
    X2 = (MC * 2048) // (X1 + MD)
    B5 = X1 + X2
    temp_c = (B5 + 8) / 160.0
    if -40 < temp_c < 85:
        print('Temperature:PASS:{:.1f} C (valid range)'.format(temp_c))
    else:
        print('Temperature:FAIL:{:.1f} C out of range'.format(temp_c))
except Exception as e:
    print('Temperature:FAIL:{}'.format(e))
try:
    ps = []
    for _ in range(10):
        i2c.writeto_mem(0x77, 0xF4, bytes([0xF4]))
        time.sleep_ms(26)
        r = i2c.readfrom_mem(0x77, 0xF6, 3)
        p = ((r[0] << 16) | (r[1] << 8) | r[2]) >> 5
        ps.append(p)
    noise = max(ps) - min(ps)
    avg = sum(ps) // len(ps)
    if noise < 200:
        print('Press Noise:PASS:{} LSB noise, avg={}'.format(noise, avg))
    else:
        print('Press Noise:FAIL:{} LSB noise (>200), avg={}'.format(noise, avg))
except Exception as e:
    print('Press Noise:FAIL:{}'.format(e))
`;

/** Detailed SD card check: SPI init, mount, capacity, write test, file list */
export const SD_DETAIL_CODE = `\
import os
from machine import SPI, Pin
import time
try:
    os.umount('/sd')
except:
    pass
try:
    cs = Pin(17, Pin.OUT)
    cs.value(1)
    time.sleep_ms(100)
    spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
    cs.value(1)
    spi.write(b'\\xff' * 10)
    time.sleep_ms(10)
    print('SPI Init:PASS:SPI0 400kHz SCK=GP18')
except Exception as e:
    print('SPI Init:FAIL:{}'.format(e))
    raise SystemExit
try:
    import sdcard
    print('SD Driver:PASS:sdcard module loaded')
except ImportError:
    print('SD Driver:FAIL:sdcard module not found')
    raise SystemExit
try:
    sd = sdcard.SDCard(spi, cs)
    vfs = os.VfsFat(sd)
    os.mount(vfs, '/sd')
    print('Mount:PASS:FAT filesystem mounted at /sd')
except Exception as e:
    print('Mount:FAIL:{}'.format(e))
    raise SystemExit
try:
    st = os.statvfs('/sd')
    total = (st[0] * st[2]) // (1024*1024)
    free = (st[0] * st[3]) // (1024*1024)
    pct = (free * 100) // total if total > 0 else 0
    if free > 10:
        print('Capacity:PASS:{} MB total, {} MB free ({}%)'.format(total, free, pct))
    else:
        print('Capacity:FAIL:{} MB free — too low'.format(free))
except Exception as e:
    print('Capacity:FAIL:{}'.format(e))
try:
    with open('/sd/_test.tmp', 'wb') as f:
        f.write(b'AVIONICS_HW_CHECK_OK')
    with open('/sd/_test.tmp', 'rb') as f:
        d = f.read()
    os.remove('/sd/_test.tmp')
    if d == b'AVIONICS_HW_CHECK_OK':
        print('Write Test:PASS:20 bytes write/read verified')
    else:
        print('Write Test:FAIL:Data mismatch on readback')
except Exception as e:
    print('Write Test:FAIL:{}'.format(e))
try:
    files = os.listdir('/sd')
    logs = [f for f in files if f.endswith('.bin')]
    print('Flight Logs:PASS:{} .bin files found'.format(len(logs)))
except Exception as e:
    print('Flight Logs:FAIL:{}'.format(e))
try:
    os.umount('/sd')
except:
    pass
`;

/** Detailed ADC check: per-rail raw+converted+range check */
export const ADC_DETAIL_CODE = `\
from machine import ADC, Pin
for name, pin, div, lo, hi in [('3V3', 28, 1.0, 3.0, 3.6), ('5V', 26, 1.735, 4.5, 5.5), ('9V', 27, 3.0, 8.0, 10.0)]:
    try:
        adc = ADC(Pin(pin))
        raw = adc.read_u16()
        v_adc = (raw / 65535) * 3.3
        v_actual = v_adc * div
        if lo <= v_actual <= hi:
            print('{} Rail:PASS:raw={} adc={:.3f}V actual={:.2f}V ({:.1f}-{:.1f}V)'.format(name, raw, v_adc, v_actual, lo, hi))
        else:
            print('{} Rail:FAIL:raw={} actual={:.2f}V outside {:.1f}-{:.1f}V'.format(name, raw, v_actual, lo, hi))
    except Exception as e:
        print('{} Rail:FAIL:{}'.format(name, e))
`;

/** List flight folders and files on SD card */
export const SD_LIST_CODE = `\
import os
from machine import SPI, Pin
import sdcard
import time
try:
    os.umount('/sd')
except:
    pass
cs = Pin(17, Pin.OUT)
cs.value(1)
time.sleep_ms(100)
spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs.value(1)
spi.write(b'\\xff' * 10)
time.sleep_ms(10)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
st = os.statvfs('/sd')
total = (st[0] * st[2]) // (1024*1024)
free = (st[0] * st[3]) // (1024*1024)
print('CAP:{},{}'.format(total, free))
entries = sorted(os.listdir('/sd'))
for e in entries:
    path = '/sd/' + e
    try:
        s = os.stat(path)
        if s[0] & 0x4000:
            tsz = 0
            try:
                for sub in os.listdir(path):
                    try:
                        tsz += os.stat(path + '/' + sub)[6]
                    except:
                        pass
            except:
                pass
            print('DIR:{}:{}'.format(e, tsz))
            try:
                for sub in sorted(os.listdir(path)):
                    try:
                        sz = os.stat(path + '/' + sub)[6]
                        print('DIRFILE:{}:{}:{}'.format(e, sub, sz))
                    except:
                        print('DIRFILE:{}:{}:0'.format(e, sub))
            except:
                pass
        else:
            print('FILE:{}:{}'.format(e, s[6]))
    except:
        pass
# Predict next flight folder
override = None
try:
    with open('/sd/_flight_name.txt', 'r') as nf:
        override = nf.read().strip()
except:
    pass
if override:
    print('NEXT:{}/flight.bin'.format(override))
    print('OVERRIDE:{}'.format(override))
else:
    idx = 1
    while True:
        d = '/sd/flight_{:03d}'.format(idx)
        try:
            os.stat(d)
            idx += 1
        except:
            break
    print('NEXT:flight_{:03d}/flight.bin'.format(idx))
os.umount('/sd')
`;

/** Write a flight name override file to SD card */
export const SD_SET_NAME_CODE = (name: string) => `\
import os
from machine import SPI, Pin
import sdcard
import time
try:
    os.umount('/sd')
except:
    pass
cs = Pin(17, Pin.OUT)
cs.value(1)
time.sleep_ms(100)
spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs.value(1)
spi.write(b'\\xff' * 10)
time.sleep_ms(10)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
with open('/sd/_flight_name.txt', 'w') as f:
    f.write('${name}')
os.umount('/sd')
print('OK')
`;

/** Clear the flight name override file from SD card */
export const SD_CLEAR_NAME_CODE = `\
import os
from machine import SPI, Pin
import sdcard
import time
try:
    os.umount('/sd')
except:
    pass
cs = Pin(17, Pin.OUT)
cs.value(1)
time.sleep_ms(100)
spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs.value(1)
spi.write(b'\\xff' * 10)
time.sleep_ms(10)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
try:
    os.remove('/sd/_flight_name.txt')
    print('OK')
except:
    print('OK')
os.umount('/sd')
`;

/** Wipe flight folders and legacy .bin files from the SD card */
export const SD_WIPE_CODE = `\
import os
from machine import SPI, Pin
import sdcard
import time
try:
    os.umount('/sd')
except:
    pass
cs = Pin(17, Pin.OUT)
cs.value(1)
time.sleep_ms(100)
spi = SPI(0, baudrate=400000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs.value(1)
spi.write(b'\\xff' * 10)
time.sleep_ms(10)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
removed = 0
entries = os.listdir('/sd')
for e in entries:
    path = '/sd/' + e
    try:
        s = os.stat(path)
        if s[0] & 0x4000:
            for sub in os.listdir(path):
                try:
                    os.remove(path + '/' + sub)
                except:
                    pass
            os.rmdir(path)
            removed += 1
        elif e.endswith('.bin'):
            os.remove(path)
            removed += 1
    except:
        pass
st = os.statvfs('/sd')
free = (st[0] * st[3]) // (1024*1024)
os.umount('/sd')
print('WIPE:{},{}'.format(removed, free))
`;

/** Write manual override flag so main.py skips fatal halts */
export const WRITE_OVERRIDE_FLAG_CODE = `\
import os
try:
    with open('_manual_override', 'w') as f:
        f.write('1')
    print('OK')
except Exception as e:
    print('ERR:{}'.format(e))
`;

/** Soft reset: reboots Pico into main.py while keeping USB CDC alive */
export const SOFT_RESET_CODE = 'import machine\nmachine.soft_reset()';

/** Detailed LED check: pin init, on/off toggle, visual blink */
export const LED_DETAIL_CODE = `\
from machine import Pin
import time
try:
    led = Pin(25, Pin.OUT)
    print('Pin Init:PASS:GP25 output mode')
except Exception as e:
    print('Pin Init:FAIL:{}'.format(e))
    raise SystemExit
try:
    led.on()
    time.sleep_ms(1)
    v = led.value()
    led.off()
    if v == 1:
        print('LED On:PASS:Pin reads HIGH when set')
    else:
        print('LED On:FAIL:Pin did not read HIGH')
except Exception as e:
    print('LED On:FAIL:{}'.format(e))
try:
    led.off()
    time.sleep_ms(1)
    v = led.value()
    if v == 0:
        print('LED Off:PASS:Pin reads LOW when cleared')
    else:
        print('LED Off:FAIL:Pin did not read LOW')
except Exception as e:
    print('LED Off:FAIL:{}'.format(e))
try:
    for _ in range(3):
        led.on()
        time.sleep_ms(200)
        led.off()
        time.sleep_ms(200)
    print('Blink Test:PASS:3 blinks completed')
except Exception as e:
    print('Blink Test:FAIL:{}'.format(e))
`;

