#!/usr/bin/env python3
"""
Pre-Flight Ground Station TUI — Step-by-step checklist wizard for MPR Altitude Logger.

Connects to the Pico over USB serial raw REPL, runs hardware checks,
then enters live monitoring with GO/NO-GO assessment.

Usage:
    python tools/preflight.py [--port /dev/cu.usbmodemXXXX]

Dependencies: rich, pyserial
"""

import sys
import os
import time
import math
import glob
import argparse
import select
import termios
import tty
from collections import deque

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Missing pyserial: pip install pyserial")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    print("Missing rich: pip install rich")
    sys.exit(1)


# -- Constants ---------------------------------------------------------------

BAUD = 115200
POLL_HZ = 2
SPARKLINE_LEN = 40
SPARKLINE_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

RAIL_SPECS = {
    "3V3": (3.3, 3.0, 3.6, 1.0),   # (nominal, min, max, divider)
    "5V":  (5.0, 4.5, 5.5, 2.0),
    "9V":  (9.0, 8.0, 10.0, 3.0),
}

# -- Pico code snippets (sent via raw REPL) ----------------------------------

SYSINFO_CODE = """\
import sys, gc, machine
gc.collect()
print('{},{},{}'.format(sys.version, machine.freq(), gc.mem_free()))
"""

I2C_SCAN_CODE = """\
from machine import SoftI2C, Pin
i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
devs = i2c.scan()
print(','.join(str(d) for d in devs))
"""

BARO_CHECK_CODE = """\
from machine import SoftI2C, Pin
i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
cid = i2c.readfrom_mem(0x77, 0xD0, 1)[0]
print(cid)
"""

SD_CHECK_CODE = """\
import os
from machine import SPI, Pin
import sdcard
spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=1)
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
logs = [f for f in os.listdir('/sd') if f.endswith('.bin')]
os.umount('/sd')
print('{},{},{},{}'.format(total, free, d == b'OK', '|'.join(sorted(logs))))
"""

ADC_CHECK_CODE = """\
from machine import ADC, Pin
a3 = ADC(Pin(28)).read_u16()
a5 = ADC(Pin(26)).read_u16()
a9 = ADC(Pin(27)).read_u16()
print('{},{},{}'.format(a3, a5, a9))
"""

INIT_CODE = r"""
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
"""

CALIBRATE_CODE = """
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
"""


# -- Raw REPL link -----------------------------------------------------------

class PicoLink:
    """Raw REPL communication with a MicroPython Pico over USB serial."""

    def __init__(self, port=None):
        self.port = port
        self.ser = None

    def find_port(self):
        candidates = glob.glob("/dev/cu.usbmodem*")
        if candidates:
            return candidates[0]
        for p in serial.tools.list_ports.comports():
            if "usbmodem" in p.device:
                return p.device
        return None

    def connect(self):
        port = self.port or self.find_port()
        if not port:
            raise ConnectionError("No Pico found on /dev/cu.usbmodem*")
        self.ser = serial.Serial(port, BAUD, timeout=1)
        self.port = port
        time.sleep(0.1)
        # Interrupt running program
        self.ser.write(b'\r\x03\x03')
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        # Enter raw REPL
        self.ser.write(b'\x01')
        time.sleep(0.5)
        self._drain()

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(b'\x02')
                time.sleep(0.1)
            except Exception:
                pass
            self.ser.close()
        self.ser = None

    def _drain(self):
        time.sleep(0.05)
        while self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)
            time.sleep(0.02)

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def exec_raw(self, code, timeout=5.0):
        """Execute code via raw REPL. Returns (stdout, stderr)."""
        if not self.connected:
            raise ConnectionError("Not connected")
        data = code.encode()
        for i in range(0, len(data), 256):
            self.ser.write(data[i:i + 256])
            time.sleep(0.01)
        self.ser.write(b'\x04')

        buf = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ser.in_waiting:
                buf += self.ser.read(self.ser.in_waiting)
                if buf.count(b'\x04') >= 2 and buf.endswith(b'>'):
                    break
            else:
                time.sleep(0.01)

        if b'OK' not in buf:
            raise RuntimeError(f"No OK in raw REPL response ({len(buf)}B): {buf[:120]!r}")

        after_ok = buf.split(b'OK', 1)[1]
        parts = after_ok.split(b'\x04')
        stdout = parts[0].decode(errors='replace').strip() if parts else ''
        stderr = parts[1].decode(errors='replace').strip() if len(parts) > 1 else ''
        return stdout, stderr


# -- Helpers -----------------------------------------------------------------

def pressure_to_altitude(pressure_pa, ground_pa):
    if pressure_pa <= 0 or ground_pa <= 0:
        return 0.0
    return 44330.0 * (1.0 - (pressure_pa / ground_pa) ** 0.1903)


def raw_to_voltage(raw, divider):
    return (raw / 65535) * 3.3 * divider


def sparkline(values, width=SPARKLINE_LEN):
    if not values:
        return "\u2581" * width
    vals = list(values)[-width:]
    lo, hi = min(vals), max(vals)
    span = hi - lo if hi > lo else 1.0
    n = len(SPARKLINE_CHARS) - 1
    out = ""
    for v in vals:
        idx = max(0, min(n, int((v - lo) / span * n)))
        out += SPARKLINE_CHARS[idx]
    return out.ljust(width)


def voltage_bar(actual, nominal, min_ok, max_ok, width=32):
    ratio = actual / nominal if nominal else 0
    filled = max(0, min(width, int(ratio * width)))
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    if actual < min_ok or actual > max_ok:
        return bar, "red", "WARN"
    elif actual < min_ok * 1.05 or actual > max_ok * 0.95:
        return bar, "yellow", "OK"
    else:
        return bar, "green", "OK"


def spinner_char():
    return SPINNER_FRAMES[int(time.monotonic() * 8) % len(SPINNER_FRAMES)]


def get_key_nonblocking():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None


# -- Check results -----------------------------------------------------------

# status: "pass", "fail", "skip", "running", "pending"
def make_check(name):
    return {"name": name, "status": "pending", "detail": ""}


# -- Preflight TUI -----------------------------------------------------------

class PreflightTUI:
    """Pre-flight checklist wizard with live monitoring."""

    def __init__(self, port=None):
        self.link = PicoLink(port)
        self.console = Console()

        # System info
        self.fw_version = ""
        self.fw_freq = ""
        self.mem_free = 0

        # Hardware checks
        self.checks = [
            make_check("I2C Bus"),
            make_check("Barometer"),
            make_check("SD Card"),
            make_check("Voltages"),
        ]
        self.sd_total = 0
        self.sd_free = 0
        self.next_log = ""

        # Telemetry state
        self.pressure = 0.0
        self.temp = 0.0
        self.alt = 0.0
        self.velocity = 0.0
        self.v3 = 0.0
        self.v5 = 0.0
        self.v9 = 0.0
        self.ground_pa = 0.0
        self.samples = 0
        self.alt_history = deque(maxlen=SPARKLINE_LEN)
        self.prev_alt = None
        self.prev_time = None
        self.sensors_inited = False

        # UI
        self.phase = "connect"  # connect, checks, live
        self.busy = ""
        self.issues = []

    # -- Connection ----------------------------------------------------------

    def do_connect(self):
        """Phase 1: connect to Pico, read system info."""
        self.busy = "Searching for Pico..."
        try:
            self.link.connect()
        except Exception as e:
            self.busy = ""
            self.issues.append(f"Connection failed: {e}")
            return False

        self.busy = "Reading system info..."
        try:
            stdout, stderr = self.link.exec_raw(SYSINFO_CODE, timeout=5.0)
            if stderr:
                self.issues.append(f"System info error: {stderr}")
                self.busy = ""
                return False
            parts = stdout.strip().split(',', 2)
            self.fw_version = parts[0].strip() if len(parts) > 0 else "?"
            freq = int(parts[1]) if len(parts) > 1 else 0
            self.fw_freq = f"{freq // 1_000_000} MHz"
            self.mem_free = int(parts[2]) if len(parts) > 2 else 0
        except Exception as e:
            self.issues.append(f"System info: {e}")
            self.busy = ""
            return False

        self.busy = ""
        self.phase = "checks"
        return True

    # -- Hardware checks -----------------------------------------------------

    def _get_check(self, name):
        for c in self.checks:
            if c["name"] == name:
                return c
        return None

    def run_all_checks(self):
        """Phase 2: run hardware checks sequentially."""
        # Reset
        for c in self.checks:
            c["status"] = "pending"
            c["detail"] = ""
        self.issues = []

        self._check_i2c()
        self._check_barometer()
        self._check_sd()
        self._check_adc()

        self.phase = "live"

    def _check_i2c(self):
        chk = self._get_check("I2C Bus")
        chk["status"] = "running"
        try:
            stdout, stderr = self.link.exec_raw(I2C_SCAN_CODE, timeout=5.0)
            if stderr:
                chk["status"] = "fail"
                chk["detail"] = stderr
                self.issues.append("I2C scan failed")
                return
            addrs = [int(x) for x in stdout.strip().split(',') if x.strip()]
            if 0x77 in addrs:
                chk["status"] = "pass"
                hex_list = ', '.join(f'0x{a:02X}' for a in addrs)
                chk["detail"] = f"Found BMP180 at 0x77  [{hex_list}]"
            else:
                chk["status"] = "fail"
                chk["detail"] = f"BMP180 (0x77) not found. Got: {addrs}"
                self.issues.append("BMP180 not found on I2C")
        except Exception as e:
            chk["status"] = "fail"
            chk["detail"] = str(e)
            self.issues.append("I2C scan error")

    def _check_barometer(self):
        chk = self._get_check("Barometer")
        chk["status"] = "running"
        try:
            stdout, stderr = self.link.exec_raw(BARO_CHECK_CODE, timeout=5.0)
            if stderr:
                chk["status"] = "fail"
                chk["detail"] = stderr
                self.issues.append("Barometer chip ID read failed")
                return
            chip_id = int(stdout.strip())
            if chip_id == 0x55:
                chk["status"] = "pass"
                chk["detail"] = f"Chip ID 0x{chip_id:02X}"
            else:
                chk["status"] = "fail"
                chk["detail"] = f"Unexpected chip ID 0x{chip_id:02X} (expected 0x55)"
                self.issues.append(f"Barometer chip ID mismatch: 0x{chip_id:02X}")
        except Exception as e:
            chk["status"] = "fail"
            chk["detail"] = str(e)
            self.issues.append("Barometer check error")

    def _check_sd(self):
        chk = self._get_check("SD Card")
        chk["status"] = "running"
        try:
            stdout, stderr = self.link.exec_raw(SD_CHECK_CODE, timeout=10.0)
            if stderr:
                chk["status"] = "fail"
                chk["detail"] = stderr
                self.issues.append("SD card check failed")
                return
            parts = stdout.strip().split(',', 3)
            total = int(parts[0])
            free = int(parts[1])
            write_ok = parts[2].strip() == 'True'
            existing_logs = [f for f in parts[3].split('|') if f] if len(parts) > 3 else []
            self.sd_total = total
            self.sd_free = free
            # Predict next log filename (same logic as datalog.py)
            base, ext = "flight", "bin"
            candidate = f"{base}.{ext}"
            idx = 1
            while candidate in existing_logs:
                candidate = f"{base}_{idx:03d}.{ext}"
                idx += 1
            self.next_log = f"/sd/{candidate}"
            if write_ok and free > 10:
                chk["status"] = "pass"
                chk["detail"] = f"{total} MB total, {free} MB free → {self.next_log}"
            elif not write_ok:
                chk["status"] = "fail"
                chk["detail"] = "Write/read verification failed"
                self.issues.append("SD card write test failed")
            else:
                chk["status"] = "fail"
                chk["detail"] = f"Low space: {free} MB free"
                self.issues.append(f"SD card low space ({free} MB)")
        except Exception as e:
            chk["status"] = "fail"
            chk["detail"] = str(e)
            self.issues.append("SD card not accessible")

    def _check_adc(self):
        chk = self._get_check("Voltages")
        chk["status"] = "running"
        try:
            stdout, stderr = self.link.exec_raw(ADC_CHECK_CODE, timeout=5.0)
            if stderr:
                chk["status"] = "fail"
                chk["detail"] = stderr
                self.issues.append("ADC read failed")
                return
            parts = stdout.strip().split(',')
            a3_raw, a5_raw, a9_raw = int(parts[0]), int(parts[1]), int(parts[2])
            v3 = raw_to_voltage(a3_raw, 1.0)
            v5 = raw_to_voltage(a5_raw, 2.0)
            v9 = raw_to_voltage(a9_raw, 3.0)
            self.v3, self.v5, self.v9 = v3, v5, v9

            ok = True
            problems = []
            for label, val, spec_key in [("3V3", v3, "3V3"), ("5V", v5, "5V"), ("9V", v9, "9V")]:
                nom, lo, hi, _ = RAIL_SPECS[spec_key]
                if val < lo or val > hi:
                    ok = False
                    problems.append(f"{label}={val:.2f}V")

            if ok:
                chk["status"] = "pass"
                chk["detail"] = f"3V3={v3:.2f}V  5V={v5:.2f}V  9V={v9:.2f}V"
            else:
                chk["status"] = "fail"
                chk["detail"] = f"Out of range: {', '.join(problems)}"
                self.issues.append(f"Voltage out of spec: {', '.join(problems)}")
        except Exception as e:
            chk["status"] = "fail"
            chk["detail"] = str(e)
            self.issues.append("ADC check error")

    # -- Init sensors & calibrate for live monitoring ------------------------

    def init_live(self):
        """Send BMP180 init code and calibrate for live monitoring."""
        self.busy = "Initialising sensors..."
        try:
            stdout, stderr = self.link.exec_raw(INIT_CODE, timeout=8.0)
            if stderr:
                self.issues.append(f"Sensor init: {stderr}")
                self.busy = ""
                return False
            self.sensors_inited = True
        except Exception as e:
            self.issues.append(f"Sensor init: {e}")
            self.busy = ""
            return False

        self.busy = "Calibrating ground pressure..."
        try:
            stdout, stderr = self.link.exec_raw(CALIBRATE_CODE, timeout=10.0)
            if stdout and not stderr:
                self.ground_pa = float(stdout.strip())
                self.alt_history.clear()
                self.samples = 0
                self.prev_alt = None
                self.prev_time = None
            else:
                self.issues.append(f"Calibration failed: {stderr}")
        except Exception as e:
            self.issues.append(f"Calibration error: {e}")

        self.busy = ""
        return True

    def recalibrate(self):
        """Re-run ground pressure calibration."""
        if not self.sensors_inited:
            return
        self.busy = "Recalibrating..."
        try:
            stdout, stderr = self.link.exec_raw(CALIBRATE_CODE, timeout=10.0)
            if stdout and not stderr:
                self.ground_pa = float(stdout.strip())
                self.alt_history.clear()
                self.samples = 0
                self.prev_alt = None
                self.prev_time = None
        except Exception:
            pass
        self.busy = ""

    # -- Poll ----------------------------------------------------------------

    def poll_sensors(self):
        if not self.sensors_inited:
            return
        try:
            stdout, stderr = self.link.exec_raw("_poll()", timeout=3.0)
        except Exception:
            return
        if not stdout or stderr:
            return
        try:
            parts = stdout.strip().split(',')
            self.pressure = float(parts[0])
            self.temp = float(parts[1])
            self.v3 = raw_to_voltage(int(parts[2]), 1.0)
            self.v5 = raw_to_voltage(int(parts[3]), 2.0)
            self.v9 = raw_to_voltage(int(parts[4]), 3.0)
        except (ValueError, IndexError):
            return

        if self.ground_pa > 0:
            self.alt = pressure_to_altitude(self.pressure, self.ground_pa)
        self.alt_history.append(self.alt)
        self.samples += 1

        now = time.monotonic()
        if self.prev_alt is not None and self.prev_time is not None:
            dt = now - self.prev_time
            if dt > 0:
                self.velocity = (self.alt - self.prev_alt) / dt
        self.prev_alt = self.alt
        self.prev_time = now

    # -- Rendering -----------------------------------------------------------

    def _check_icon(self, status):
        icons = {
            "pass":    "[green][PASS][/green]",
            "fail":    "[red][FAIL][/red]",
            "skip":    "[yellow][SKIP][/yellow]",
            "running": f"[yellow]{spinner_char()}    [/yellow]",
            "pending": "[dim][ -- ][/dim]",
        }
        return icons.get(status, "[dim][ -- ][/dim]")

    def _all_checks_passed(self):
        for c in self.checks:
            if c["status"] == "fail":
                return False
        return True

    def _voltages_ok(self):
        for label, spec_key in [("3V3", "3V3"), ("5V", "5V"), ("9V", "9V")]:
            nom, lo, hi, _ = RAIL_SPECS[spec_key]
            val = {"3V3": self.v3, "5V": self.v5, "9V": self.v9}[spec_key]
            if val > 0 and (val < lo or val > hi):
                return False
        return True

    def _baro_sane(self):
        return 80000 < self.pressure < 110000 if self.pressure > 0 else True

    def render(self):
        lines = []

        # -- SYSTEM --
        lines.append("[bold]SYSTEM[/bold]")
        if self.link.connected:
            lines.append(f"  Board     [green]\u25cf[/green] Connected  {self.link.port}")
        else:
            lines.append("  Board     [red]\u25cf[/red] Disconnected")

        if self.fw_version:
            # Truncate long version strings
            ver = self.fw_version
            if len(ver) > 40:
                ver = ver[:40] + "..."
            lines.append(f"  Firmware  {ver}  @ {self.fw_freq}")
        else:
            lines.append("  Firmware  [dim]--[/dim]")

        if self.mem_free > 0:
            lines.append(f"  Memory    {self.mem_free:,} bytes free")
        else:
            lines.append("  Memory    [dim]--[/dim]")
        lines.append("")

        # -- HARDWARE CHECKS --
        lines.append("[bold]HARDWARE CHECKS[/bold]")
        for c in self.checks:
            icon = self._check_icon(c["status"])
            detail = f"  {c['detail']}" if c["detail"] else ""
            lines.append(f"  {c['name']:<12s} {icon}{detail}")
        lines.append("")

        # -- LIVE TELEMETRY (only in live phase) --
        if self.phase == "live" and self.sensors_inited:
            lines.append("[bold]LIVE TELEMETRY[/bold]")
            lines.append(
                f"  Pressure  {self.pressure:>8.0f} Pa"
                f"     Temperature  {self.temp:>5.1f} \u00b0C"
            )
            lines.append(
                f"  Altitude  {self.alt:>8.1f} m AGL"
                f"   Velocity    {self.velocity:>+5.1f} m/s"
            )
            spark = sparkline(self.alt_history)
            alt_label = f"{self.alt:.1f}m" if self.samples > 0 else "--"
            lines.append(f"  Alt {spark} {alt_label}")
            lines.append("")

            # -- POWER RAILS --
            lines.append("[bold]POWER RAILS[/bold]")
            for label, actual, spec_key in [
                ("3V3", self.v3, "3V3"),
                ("5V ", self.v5, "5V"),
                ("9V ", self.v9, "9V"),
            ]:
                nom, lo, hi, _ = RAIL_SPECS[spec_key]
                bar, color, status = voltage_bar(actual, nom, lo, hi)
                lines.append(
                    f"  {label} [{color}]{bar}[/{color}]"
                    f"  {actual:.2f}V  {status}"
                )
            lines.append("")

            # -- GO / NO-GO --
            go = (
                self._all_checks_passed()
                and self._voltages_ok()
                and self._baro_sane()
                and self.sd_free > 10
            )
            if go and not self.issues:
                npass = sum(1 for c in self.checks if c["status"] in ("pass", "skip"))
                lines.append(
                    "  [on green][bold black]"
                    "  \u2605  GO FOR LAUNCH  \u2605                              "
                    "[/bold black][/on green]"
                )
                lines.append(
                    f"  [on green][bold black]"
                    f"  All {npass} checks passed  \u2022  Systems nominal              "
                    f"[/bold black][/on green]"
                )
            else:
                reasons = list(self.issues)
                if not self._voltages_ok():
                    reasons.append("Voltage rail out of spec")
                if not self._baro_sane() and self.pressure > 0:
                    reasons.append("Barometer reading out of range")
                if 0 < self.sd_free <= 10:
                    reasons.append("SD card low space")
                # deduplicate
                seen = set()
                unique = []
                for r in reasons:
                    if r not in seen:
                        seen.add(r)
                        unique.append(r)
                reason_str = "; ".join(unique[:3]) if unique else "Check failures"
                lines.append(
                    "  [on red][bold black]"
                    "  \u2717  NO-GO  \u2717                                        "
                    "[/bold black][/on red]"
                )
                lines.append(
                    f"  [on red][bold black]"
                    f"  {reason_str:<52s}"
                    f"[/bold black][/on red]"
                )
            lines.append("")

        # -- Busy spinner --
        if self.busy:
            lines.append(f"  [yellow]{spinner_char()} {self.busy}[/yellow]")
            lines.append("")

        # -- Controls --
        if self.phase == "live":
            lines.append(
                "  [bold][R][/bold] Recalibrate  "
                "[bold][T][/bold] Re-test  "
                "[bold][Q][/bold] Quit"
            )
            lines.append(
                f"  [dim]{POLL_HZ} Hz \u2022 {self.samples} samples"
                f" \u2022 ground: {self.ground_pa:.0f} Pa[/dim]"
            )
        elif self.phase == "checks":
            lines.append("  [dim]Running checks...[/dim]")
        else:
            lines.append("  [dim]Connecting...[/dim]")

        body = "\n".join(lines)
        return Panel(
            body,
            title="[bold white] UNSW ROCKETRY \u2014 PRE-FLIGHT CHECK [/bold white]",
            border_style="blue",
            width=68,
            padding=(1, 2),
        )

    # -- Key handling --------------------------------------------------------

    def handle_key(self, key):
        """Returns False to quit."""
        k = key.lower()
        if k == 'q':
            return False
        elif k == 'r' and self.phase == "live":
            self.recalibrate()
        elif k == 't' and self.phase == "live":
            self.run_all_checks()
            self.init_live()
        return True


# -- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="UNSW Rocketry - Pre-Flight Check TUI"
    )
    parser.add_argument("--port", help="Serial port (auto-detect if omitted)")
    args = parser.parse_args()

    tui = PreflightTUI(port=args.port)
    console = Console()

    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())

        with Live(tui.render(), console=console, refresh_per_second=8,
                  transient=True) as live:

            # Phase 1: connect
            tui.do_connect()
            live.update(tui.render())

            if not tui.link.connected:
                # Show final state then exit
                live.update(tui.render())
                time.sleep(2)
                return

            # Phase 2: hardware checks
            for c in tui.checks:
                c["status"] = "pending"
            live.update(tui.render())

            # Run checks one at a time with render updates between
            tui._check_i2c()
            live.update(tui.render())
            tui._check_barometer()
            live.update(tui.render())
            tui._check_sd()
            live.update(tui.render())
            tui._check_adc()
            live.update(tui.render())

            tui.phase = "live"

            # Phase 3: init sensors for live monitoring
            tui.init_live()
            live.update(tui.render())

            # Phase 4: live loop
            last_poll = 0
            poll_interval = 1.0 / POLL_HZ

            while True:
                key = get_key_nonblocking()
                if key:
                    if not tui.handle_key(key):
                        break
                    live.update(tui.render())

                now = time.monotonic()
                if now - last_poll >= poll_interval:
                    last_poll = now
                    tui.poll_sensors()

                live.update(tui.render())
                time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        tui.link.close()
        console.print("\n[dim]Disconnected.[/dim]")


if __name__ == "__main__":
    main()
