#!/usr/bin/env python3
"""
Ground Station TUI — Terminal dashboard for MPR Altitude Logger.

Connects to the Pico over USB serial, polls sensors via raw REPL,
and displays live telemetry in a rich terminal UI.

Usage:
    python tools/tui.py [--port /dev/cu.usbmodemXXXX]

Dependencies: rich, pyserial
"""

import sys
import os
import time
import math
import glob
import struct
import subprocess
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
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.layout import Layout
    from rich.columns import Columns
except ImportError:
    print("Missing rich: pip install rich")
    sys.exit(1)


# ── Constants ───────────────────────────────────────────────────

BAUD = 115200
POLL_HZ = 2
SPARKLINE_LEN = 50
SPARKLINE_CHARS = " ▁▂▃▄▅▆▇█"

# Voltage rail specs: (nominal, min_ok, max_ok)
RAIL_SPECS = {
    "3V3": (3.3, 3.0, 3.6),
    "5V":  (5.0, 4.5, 5.5),
    "9V":  (9.0, 8.0, 10.0),
}

# BMP180 init code sent to Pico once on connect.
# Sets up I2C, reads calibration, defines a fast _poll() function.
INIT_CODE = r"""
import struct, time
from machine import SoftI2C, Pin, ADC

_i2c = SoftI2C(sda=Pin(4), scl=Pin(5), freq=100000)
_addr = 0x77

# Read calibration
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
    # Temperature
    _i2c.writeto_mem(_addr, 0xF4, b'\x2e')
    time.sleep_ms(5)
    r = _i2c.readfrom_mem(_addr, 0xF6, 2)
    UT = (r[0] << 8) | r[1]
    X1 = (UT - _AC6) * _AC5 // 32768
    X2 = (_MC * 2048) // (X1 + _MD)
    B5 = X1 + X2
    t = (B5 + 8) / 160.0
    # Pressure (oss=3)
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
    # ADC reads
    v3 = _a3v.read_u16()
    v5 = _a5v.read_u16()
    v9 = _a9v.read_u16()
    print('{},{:.1f},{},{},{}'.format(p, t, v3, v5, v9))
"""

POLL_CMD = "_poll()"

# Calibrate on the Pico side — one round-trip instead of 20
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


# ── Pico Link ──────────────────────────────────────────────────

class PicoLink:
    """Manages raw REPL communication with Pico over USB serial."""

    def __init__(self, port=None):
        self.port = port
        self.ser = None
        self.initialized = False

    def find_port(self):
        """Auto-detect Pico serial port."""
        candidates = glob.glob("/dev/cu.usbmodem*")
        if candidates:
            return candidates[0]
        for p in serial.tools.list_ports.comports():
            if "usbmodem" in p.device:
                return p.device
        return None

    def connect(self):
        """Open serial and enter raw REPL."""
        port = self.port or self.find_port()
        if not port:
            raise ConnectionError("No Pico found")

        self.ser = serial.Serial(port, BAUD, timeout=1)
        self.port = port
        time.sleep(0.1)

        # Interrupt any running program
        self.ser.write(b'\r\x03\x03')
        time.sleep(0.5)
        self.ser.reset_input_buffer()

        # Enter raw REPL (Ctrl-A)
        self.ser.write(b'\x01')
        time.sleep(0.5)
        # Consume the raw REPL banner ("raw REPL; CTRL-B to exit\r\n>")
        self._drain()

        self.initialized = False

    def close(self):
        """Exit raw REPL and close serial."""
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(b'\x02')
                time.sleep(0.1)
            except Exception:
                pass
            self.ser.close()
        self.ser = None
        self.initialized = False

    def _drain(self):
        """Read and discard everything currently in the serial buffer."""
        time.sleep(0.05)
        while self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)
            time.sleep(0.02)

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def exec_raw(self, code, timeout=5.0):
        """Execute code in raw REPL. Returns (stdout, stderr).

        Sends code in 256-byte chunks (raw REPL flow control),
        then reads the OK<stdout>\\x04<stderr>\\x04> response.
        """
        if not self.connected:
            raise ConnectionError("Not connected")

        # Send code in 256-byte chunks for flow control
        data = code.encode()
        for i in range(0, len(data), 256):
            self.ser.write(data[i:i+256])
            time.sleep(0.01)
        # Ctrl-D to execute
        self.ser.write(b'\x04')

        # Read full response: OK<stdout>\x04<stderr>\x04>
        buf = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ser.in_waiting:
                buf += self.ser.read(self.ser.in_waiting)
                # Complete response has OK, two \x04 markers, and ends with >
                if buf.count(b'\x04') >= 2 and buf.endswith(b'>'):
                    break
            else:
                time.sleep(0.01)

        if b'OK' not in buf:
            raise RuntimeError(f"No OK from raw REPL ({len(buf)}B): {buf[:100]!r}")

        after_ok = buf.split(b'OK', 1)[1]
        parts = after_ok.split(b'\x04')
        stdout = parts[0].decode(errors='replace').strip() if parts else ''
        stderr = parts[1].decode(errors='replace').strip() if len(parts) > 1 else ''

        return stdout, stderr

    def init_sensors(self):
        """Send init code to set up sensors and define _poll()."""
        stdout, stderr = self.exec_raw(INIT_CODE, timeout=8.0)
        if stderr:
            raise RuntimeError(f"Init failed: {stderr}")
        self.initialized = True

    def poll(self):
        """Call _poll() and parse CSV result.

        Returns dict with keys: pressure, temp, v3_raw, v5_raw, v9_raw
        or None on failure.
        """
        stdout, stderr = self.exec_raw(POLL_CMD, timeout=3.0)
        if stderr or not stdout:
            return None

        try:
            parts = stdout.strip().split(',')
            return {
                'pressure': float(parts[0]),
                'temp': float(parts[1]),
                'v3_raw': int(parts[2]),
                'v5_raw': int(parts[3]),
                'v9_raw': int(parts[4]),
            }
        except (ValueError, IndexError):
            return None


# ── Helpers ────────────────────────────────────────────────────

def pressure_to_altitude(pressure_pa, ground_pa):
    """Hypsometric formula: pressure -> altitude AGL (m)."""
    if pressure_pa <= 0 or ground_pa <= 0:
        return 0.0
    return 44330.0 * (1.0 - (pressure_pa / ground_pa) ** 0.1903)


def raw_to_voltage(raw, divider):
    """Convert ADC raw u16 to actual voltage."""
    return (raw / 65535) * 3.3 * divider


def sparkline(values, width=SPARKLINE_LEN):
    """Generate unicode sparkline from a sequence of floats."""
    if not values:
        return " " * width
    vals = list(values)[-width:]
    lo, hi = min(vals), max(vals)
    span = hi - lo if hi > lo else 1.0
    chars = SPARKLINE_CHARS
    n = len(chars) - 1
    line = ""
    for v in vals:
        idx = int((v - lo) / span * n)
        idx = max(0, min(n, idx))
        line += chars[idx]
    return line.ljust(width)


def voltage_bar(actual, nominal, min_ok, max_ok, width=30):
    """Render a voltage bar with color."""
    ratio = actual / nominal if nominal else 0
    filled = int(ratio * width)
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)

    if actual < min_ok or actual > max_ok:
        color = "red"
        status = "WARN"
    elif actual < min_ok * 1.05 or actual > max_ok * 0.95:
        color = "yellow"
        status = "OK"
    else:
        color = "green"
        status = "OK"

    return bar, color, status


def get_key_nonblocking():
    """Non-blocking single keypress read on macOS/Linux."""
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None


# ── Dashboard ──────────────────────────────────────────────────

class Dashboard:
    """State management and rendering for the ground station TUI."""

    def __init__(self, port=None):
        self.link = PicoLink(port)
        self.console = Console()

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

        # UI state
        self.status_msg = ""
        self.status_style = "dim"
        self.error_msg = ""
        self.overlay_text = ""  # modal overlay content
        self.busy = ""  # non-empty = show spinner with this message
        self.connected = False
        self.firmware_ok = False

    def try_connect(self):
        """Attempt to connect and init sensors."""
        self.busy = "Connecting to Pico..."
        try:
            self.link.connect()
            self.connected = True
            self.error_msg = ""
        except Exception as e:
            self.connected = False
            self.error_msg = str(e)
            self.busy = ""
            return False

        self.busy = "Initialising sensors..."
        try:
            self.link.init_sensors()
            self.firmware_ok = True
            self.set_status("Connected", "green")
        except Exception as e:
            self.firmware_ok = False
            self.error_msg = f"Sensor init: {e}"
            self.set_status("Sensor init failed", "red")
            self.busy = ""
            return False

        self.busy = ""
        self.calibrate()
        return True

    def calibrate(self):
        """Average pressure samples on-Pico for ground reference (single round-trip)."""
        self.busy = "Calibrating ground pressure..."
        try:
            stdout, stderr = self.link.exec_raw(CALIBRATE_CODE, timeout=10.0)
            if stdout and not stderr:
                self.ground_pa = float(stdout.strip())
                self.alt_history.clear()
                self.samples = 0
                self.prev_alt = None
                self.prev_time = None
                self.set_status("Ready", "green")
            else:
                self.set_status(f"Calibration failed: {stderr}", "red")
        except Exception as e:
            self.set_status(f"Calibration error: {e}", "red")
        finally:
            self.busy = ""

    def poll_sensors(self):
        """Single poll cycle — update state from Pico data."""
        if not self.link.connected or not self.link.initialized:
            return

        try:
            data = self.link.poll()
        except Exception:
            self.connected = False
            self.set_status("Disconnected", "red")
            return

        if data is None:
            return

        self.pressure = data['pressure']
        self.temp = data['temp']
        self.v3 = raw_to_voltage(data['v3_raw'], 1.0)
        self.v5 = raw_to_voltage(data['v5_raw'], 2.0)
        self.v9 = raw_to_voltage(data['v9_raw'], 3.0)

        if self.ground_pa > 0:
            self.alt = pressure_to_altitude(self.pressure, self.ground_pa)
        self.alt_history.append(self.alt)
        self.samples += 1

        # Numerical velocity from altitude difference
        now = time.monotonic()
        if self.prev_alt is not None and self.prev_time is not None:
            dt = now - self.prev_time
            if dt > 0:
                self.velocity = (self.alt - self.prev_alt) / dt
        self.prev_alt = self.alt
        self.prev_time = now

    def set_status(self, msg, style="dim"):
        self.status_msg = msg
        self.status_style = style

    def run_hw_test(self):
        """Execute hw_check.py on the Pico via raw REPL."""
        if not self.link.connected:
            self.overlay_text = "[red]Not connected[/red]"
            return
        self.busy = "Running hardware test..."

        try:
            # Read hw_check.py source from local filesystem and exec on Pico
            hw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hw_check.py")
            if not os.path.exists(hw_path):
                # Fallback: try exec("import hw_check; hw_check.run()")
                stdout, stderr = self.link.exec_raw(
                    "exec(open('hw_check.py').read())", timeout=30.0
                )
            else:
                with open(hw_path) as f:
                    code = f.read()
                stdout, stderr = self.link.exec_raw(code, timeout=30.0)

            output = stdout if stdout else ""
            if stderr:
                output += f"\n[red]{stderr}[/red]"
            self.overlay_text = output or "[dim]No output[/dim]"
        except Exception as e:
            self.overlay_text = f"[red]HW Test error: {e}[/red]"
        finally:
            self.busy = ""

        # Re-init sensors after hw_check (it may have reset peripherals)
        try:
            self.link.init_sensors()
        except Exception:
            pass

    def run_install_sd(self):
        """Close serial, run mpremote mip install sdcard, reconnect."""
        self.busy = "Installing SD card driver..."

        port = self.link.port
        self.link.close()
        self.connected = False
        time.sleep(0.5)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "mpremote", "connect", port,
                 "mip", "install", "sdcard"],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                self.overlay_text = f"[green]SD driver installed![/green]\n{output}"
            else:
                self.overlay_text = f"[red]Install failed[/red]\n{output}"
        except Exception as e:
            self.overlay_text = f"[red]Install error: {e}[/red]"
        finally:
            self.busy = ""

        # Reconnect
        time.sleep(1.0)
        self.try_connect()

    def run_sd_files(self):
        """List files on the SD card."""
        if not self.link.connected:
            self.overlay_text = "[red]Not connected[/red]"
            return
        self.busy = "Reading SD card..."

        try:
            code = """
import os
from machine import SPI, Pin
try:
    import sdcard
except:
    print('ERR:no sdcard module')
    raise SystemExit
try:
    spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
    cs = Pin(17, Pin.OUT, value=1)
    sd = sdcard.SDCard(spi, cs)
    vfs = os.VfsFat(sd)
    os.mount(vfs, '/sd')
    st = os.statvfs('/sd')
    total = (st[0] * st[2]) / (1024*1024)
    free = (st[0] * st[3]) / (1024*1024)
    print('TOTAL:{:.0f}'.format(total))
    print('FREE:{:.0f}'.format(free))
    for f in os.listdir('/sd'):
        sz = os.stat('/sd/' + f)[6]
        print('FILE:{}:{}'.format(f, sz))
    os.umount('/sd')
except Exception as e:
    print('ERR:{}'.format(e))
"""
            stdout, stderr = self.link.exec_raw(code, timeout=10.0)

            lines = stdout.strip().split('\n') if stdout else []
            output_parts = []
            for line in lines:
                if line.startswith('TOTAL:'):
                    output_parts.append(f"Total: {line[6:]} MB")
                elif line.startswith('FREE:'):
                    output_parts.append(f"Free:  {line[5:]} MB")
                elif line.startswith('FILE:'):
                    parts = line[5:].split(':')
                    name = parts[0]
                    size = int(parts[1]) if len(parts) > 1 else 0
                    if size > 1024:
                        output_parts.append(f"  {name:30s} {size/1024:.1f} KB")
                    else:
                        output_parts.append(f"  {name:30s} {size} B")
                elif line.startswith('ERR:'):
                    output_parts.append(f"[red]{line[4:]}[/red]")

            self.overlay_text = '\n'.join(output_parts) if output_parts else "[dim]No files[/dim]"
            if stderr:
                self.overlay_text += f"\n[red]{stderr}[/red]"

            # Re-init after SD mount/unmount
            try:
                self.link.init_sensors()
            except Exception:
                pass

        except Exception as e:
            self.overlay_text = f"[red]SD error: {e}[/red]"
        finally:
            self.busy = ""

    def render(self):
        """Build the rich Panel for display."""
        lines = []

        # ── System status ──
        lines.append("[bold]SYSTEM[/bold]")

        if self.connected:
            conn_dot = "[green]●[/green]"
            conn_text = f"Connected  {self.link.port}"
        else:
            conn_dot = "[red]●[/red]"
            conn_text = self.error_msg or "Disconnected"
        lines.append(f"  Board     {conn_dot} {conn_text}")

        if self.firmware_ok:
            lines.append(f"  Firmware  [green]●[/green] Loaded")
        elif self.connected:
            lines.append(f"  Firmware  [red]●[/red] Init failed")
        else:
            lines.append(f"  Firmware  [dim]●[/dim] --")

        lines.append("")

        # ── Barometer ──
        lines.append("[bold]BAROMETER (BMP180)[/bold]")
        lines.append(
            f"  Pressure  {self.pressure:>8.0f} Pa"
            f"     Temperature  {self.temp:>5.1f} °C"
        )
        lines.append(
            f"  Altitude  {self.alt:>8.1f} m AGL"
            f"   Velocity    {self.velocity:>+5.1f} m/s"
        )
        lines.append("")

        # ── Sparkline ──
        spark = sparkline(self.alt_history)
        alt_label = f"{self.alt:.1f}m" if self.samples > 0 else "--"
        lines.append(f"  Alt {spark} {alt_label}")
        lines.append("")

        # ── Power rails ──
        lines.append("[bold]POWER RAILS[/bold]")
        for label, actual, divspec in [
            ("3V3", self.v3, RAIL_SPECS["3V3"]),
            ("5V ", self.v5, RAIL_SPECS["5V"]),
            ("9V ", self.v9, RAIL_SPECS["9V"]),
        ]:
            nom, lo, hi = divspec
            bar, color, status = voltage_bar(actual, nom, lo, hi, width=30)
            lines.append(
                f"  {label} [{color}]{bar}[/{color}]"
                f"  {actual:.2f}V  {status}"
            )

        lines.append("")

        # ── Busy indicator ──
        if self.busy:
            spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = spinner_frames[int(time.monotonic() * 8) % len(spinner_frames)]
            lines.append(f"  [yellow]{frame} {self.busy}[/yellow]")
            lines.append("")

        # ── Overlay (modal output from commands) ──
        if self.overlay_text:
            lines.append("[bold]OUTPUT[/bold] [dim](press any key to dismiss)[/dim]")
            # Limit overlay height
            overlay_lines = self.overlay_text.split('\n')
            if len(overlay_lines) > 15:
                overlay_lines = overlay_lines[:15] + ["[dim]... truncated ...[/dim]"]
            for ol in overlay_lines:
                lines.append(f"  {ol}")
            lines.append("")

        # ── Controls ──
        lines.append(
            "  [bold][T][/bold] HW Test  "
            "[bold][R][/bold] Recalibrate  "
            "[bold][I][/bold] Install SD  "
            "[bold][S][/bold] SD Files  "
            "[bold][Q][/bold] Quit"
        )

        # ── Status bar ──
        status = (
            f"  {POLL_HZ} Hz"
            f" \u2022 {self.samples} samples"
            f" \u2022 ground: {self.ground_pa:.0f} Pa"
        )
        lines.append(f"[{self.status_style}]{status}[/{self.status_style}]")

        body = "\n".join(lines)
        return Panel(
            body,
            title="[bold white] UNSW ROCKETRY — MPR ALTITUDE LOGGER [/bold white]",
            border_style="blue",
            width=66,
            padding=(1, 2),
        )

    def handle_key(self, key):
        """Process a keypress. Returns False to quit."""
        if self.overlay_text and key not in ('q', 'Q'):
            # Any key dismisses overlay
            self.overlay_text = ""
            return True

        k = key.lower()
        if k == 'q':
            return False
        elif k == 't':
            self.run_hw_test()
        elif k == 'r':
            if self.link.connected and self.link.initialized:
                self.calibrate()
        elif k == 'i':
            self.run_install_sd()
        elif k == 's':
            self.run_sd_files()
        return True


# ── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MPR Altitude Logger Ground Station")
    parser.add_argument("--port", help="Serial port (auto-detect if omitted)")
    args = parser.parse_args()

    dash = Dashboard(port=args.port)
    console = Console()

    # Save terminal settings for non-blocking input
    old_settings = termios.tcgetattr(sys.stdin)

    try:
        # Set terminal to raw mode for keypress detection
        tty.setcbreak(sys.stdin.fileno())

        dash.busy = "Searching for Pico..."

        with Live(dash.render(), console=console, refresh_per_second=8, transient=True) as live:
            # Connect inside the Live context so spinner renders
            dash.try_connect()
            live.update(dash.render())

            last_poll = 0
            last_reconnect = 0
            poll_interval = 1.0 / POLL_HZ

            while True:
                # Non-blocking key check
                key = get_key_nonblocking()
                if key:
                    if not dash.handle_key(key):
                        break
                    live.update(dash.render())

                now = time.monotonic()

                # Poll sensors at target rate
                if now - last_poll >= poll_interval:
                    last_poll = now

                    if not dash.connected:
                        # Try reconnect every 3s
                        if now - last_reconnect >= 3.0:
                            last_reconnect = now
                            try:
                                dash.try_connect()
                            except Exception:
                                pass
                    else:
                        dash.poll_sensors()

                # Always update render (for spinner animation)
                live.update(dash.render())

                # Small sleep to avoid busy-spinning
                time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        dash.link.close()
        console.print("\n[dim]Disconnected.[/dim]")


if __name__ == "__main__":
    main()
