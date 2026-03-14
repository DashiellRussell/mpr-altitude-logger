#!/usr/bin/env python3
"""
Pico Diagnostic TUI — run on-device stress tests from your laptop.

Rich-based terminal UI that connects to the Pico over USB serial
and runs hardware diagnostic tests remotely via raw REPL.

Requires firmware to be deployed first: cd tools/ground-station && pnpm deploy:pico

Usage:
    python tools/pico_diag_tui.py [--port /dev/cu.usbmodemXXXX]
    pnpm pico:diag

Dependencies: rich, pyserial
"""

import sys
import os
import time
import glob
import argparse
import select
import termios
import tty
import re

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Missing pyserial: pip install pyserial")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.layout import Layout
    from rich.table import Table
except ImportError:
    print("Missing rich: pip install rich")
    sys.exit(1)


# ── Constants ──────────────────────────────────────────────

BAUD = 115200
SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
TUI_VERSION = "1.3.0"
EXPECTED_DIAG_VERSION = "1.3.0"  # must match pico_diag.DIAG_VERSION on-device
EXPECTED_FW_VERSION = "1.9.0"   # must match config.VERSION on-device

# Diagnostic tests — each runs a function from pico_diag.py on the Pico
DIAG_TESTS = [
    {
        "key": "1",
        "name": "Sensor Bench",
        "func": "test_sensor_bench",
        "timeout": 120,
        "est_seconds": 40,
        "needs_hw": True,
        "about": [
            "Reads the BMP180 barometer 1000 times over I2C and",
            "measures read timing, pressure noise, and temperature stability.",
        ],
        "tests_for": [
            "I2C read latency (min/avg/max/std)",
            "Timing distribution — histogram of read durations",
            "Pressure noise floor (Pa std → altitude noise in metres)",
            "Temperature stability (std deviation)",
            "I2C clock stretch detection (reads > 2x average)",
        ],
    },
    {
        "key": "2",
        "name": "SD Card Bench",
        "func": "test_sd_bench",
        "timeout": 360,
        "est_seconds": 330,
        "needs_hw": True,
        "about": [
            "Tests SD card write performance in two phases: a quick",
            "1000-frame burst, then a 5-minute sustained write at 25 Hz.",
        ],
        "tests_for": [
            "Write latency per 34-byte frame (min/avg/max)",
            "Flush latency (every 25 frames)",
            "os.sync() timing",
            "Whether any single write exceeds the 40ms frame budget",
            "Sustained write stability — 30s interval reports over 5 min",
            "Total write errors over the sustained run",
        ],
    },
    {
        "key": "3",
        "name": "Loop Budget",
        "func": "test_loop_budget",
        "timeout": 120,
        "est_seconds": 45,
        "needs_hw": True,
        "about": [
            "Runs 1000 frames of the full avionics pipeline and times",
            "each stage individually to find the bottleneck.",
        ],
        "tests_for": [
            "Baro read — I2C read + conversion",
            "Altitude calc — hypsometric formula",
            "Kalman filter — predict + update cycle",
            "State machine — flight phase detection",
            "Power read — 3x ADC reads",
            "Struct pack — binary frame encoding",
            "Total vs budget headroom at current sample rate",
        ],
    },
    {
        "key": "4",
        "name": "RAM Profile",
        "func": "test_ram_profile",
        "timeout": 120,
        "est_seconds": 60,
        "needs_hw": True,
        "about": [
            "Measures memory consumption of each avionics object and",
            "runs 1000 hot-loop frames to detect memory leaks.",
        ],
        "tests_for": [
            "Per-object RAM cost (Kalman, FSM, FlightLogger, BMP180)",
            "Total RAM remaining after all objects initialised",
            "Hot-loop leak detection — gc.mem_free() every 100 frames",
            "Leak rate classification (negligible < 100 bytes / 1000 frames)",
        ],
    },
    {
        "key": "5",
        "name": "Float Precision",
        "func": "test_float_precision",
        "timeout": 60,
        "est_seconds": 15,
        "needs_hw": False,
        "about": [
            "Pure math test — no hardware needed. Runs the Kalman filter",
            "for 10000 iterations to check for 32-bit float drift.",
        ],
        "tests_for": [
            "Constant input drift — 10k iterations at 500.0 m",
            "Ramp tracking — 0 → 10000 m, expected velocity tracking",
            "Covariance matrix health — positive-definiteness check",
        ],
    },
    {
        "key": "6",
        "name": "Dual-Core Stress",
        "func": "test_dual_core",
        "timeout": 120,
        "est_seconds": 70,
        "needs_hw": True,
        "about": [
            "Measures Core 0 pipeline timing with and without Core 1",
            "running LED blink stress. Detects inter-core interference.",
        ],
        "tests_for": [
            "Core 0 solo — 30s baseline timing",
            "Core 0+1 — 30s with Core 1 LED toggle at 40 Hz",
            "Jitter increase (avg and max us)",
            "Whether dual-core max frame time stays within budget",
            "Core 1 heartbeat stability over the run",
        ],
    },
    {
        "key": "7",
        "name": "Endurance Run",
        "func": "test_endurance",
        "timeout": 660,
        "est_seconds": 600,
        "needs_hw": True,
        "about": [
            "Full pipeline stability test — runs sensor → Kalman → FSM →",
            "SD write at 25 Hz for 10 minutes with 30s interval reports.",
        ],
        "tests_for": [
            "Timing drift over 10 min (first vs last interval avg)",
            "RAM stability — gc.mem_free() trend over time",
            "Temperature drift over the run",
            "Total error count (sensor, SD, pack failures)",
            "SD sustained write with periodic flush",
        ],
    },
    {
        "key": "8",
        "name": "Error Injection",
        "func": "test_error_injection",
        "timeout": 60,
        "est_seconds": 30,
        "needs_hw": True,
        "about": [
            "Injects faults into each subsystem and verifies the avionics",
            "recovers gracefully without crashing.",
        ],
        "tests_for": [
            "I2C wrong address — OSError caught, BMP180 still works after",
            "I2C bus recovery — destroy and recreate I2C + BMP180",
            "SD unmount/remount — write fails when unmounted, works after remount",
            "Kalman bad input — inf, -inf, 1e15 don't crash the filter",
            "FSM extreme values — extreme alt/vel don't crash state machine",
        ],
    },
]


# ── PicoLink ───────────────────────────────────────────────

class PicoLink:
    """Raw REPL communication with MicroPython Pico over USB serial."""

    def __init__(self, port=None):
        self.port = port
        self.ser = None

    def find_port(self):
        candidates = glob.glob("/dev/cu.usbmodem*")
        if candidates:
            return candidates[0]
        candidates = glob.glob("/dev/ttyACM*")
        if candidates:
            return candidates[0]
        for p in serial.tools.list_ports.comports():
            if "usbmodem" in p.device or "ACM" in p.device:
                return p.device
        return None

    def connect(self):
        port = self.port or self.find_port()
        if not port:
            raise ConnectionError("No Pico found on USB")
        self.ser = serial.Serial(port, BAUD, timeout=1)
        self.port = port
        time.sleep(0.1)
        self.ser.write(b'\r\x03\x03')
        time.sleep(0.5)
        self.ser.reset_input_buffer()
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

    def exec_streaming(self, code, timeout=600.0):
        """Execute code and stream stdout back line by line (generator)."""
        if not self.connected:
            raise ConnectionError("Not connected")
        data = code.encode()
        for i in range(0, len(data), 256):
            self.ser.write(data[i:i + 256])
            time.sleep(0.01)
        self.ser.write(b'\x04')

        buf = b''
        deadline = time.monotonic() + timeout
        got_ok = False

        while time.monotonic() < deadline:
            if self.ser.in_waiting:
                chunk = self.ser.read(self.ser.in_waiting)
                buf += chunk

                if not got_ok:
                    if b'OK' in buf:
                        got_ok = True
                        buf = buf.split(b'OK', 1)[1]
                    else:
                        continue

                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    text = line.decode(errors='replace').rstrip('\r')
                    yield text

                if buf.count(b'\x04') >= 2 and buf.endswith(b'>'):
                    remaining = buf.split(b'\x04')[0].decode(errors='replace').strip()
                    if remaining:
                        for line in remaining.split('\n'):
                            yield line.rstrip('\r')
                    parts = buf.split(b'\x04')
                    stderr = parts[1].decode(errors='replace').strip() if len(parts) > 1 else ''
                    if stderr:
                        yield f"[stderr] {stderr}"
                    return
            else:
                time.sleep(0.02)
                # Yield None as a heartbeat so the caller can update its
                # timer even when the Pico isn't sending full lines yet.
                yield None

        yield "[timeout] Test exceeded time limit"


# ── Helpers ────────────────────────────────────────────────

def _fmt_time(seconds):
    """Format seconds as M:SS."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def _clear_screen():
    """Clear terminal and move cursor to top."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def spinner_char():
    return SPINNER_FRAMES[int(time.monotonic() * 8) % len(SPINNER_FRAMES)]


def strip_ansi(text):
    """Remove ANSI escape codes from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def get_sysinfo(link):
    """Get Pico system info via raw REPL."""
    # gc.collect() first to free RAM, then query in a single print
    code = "import gc\ngc.collect()\nimport sys, machine\nprint('{},{},{}'.format(sys.version, machine.freq(), gc.mem_free()))"
    stdout, stderr = link.exec_raw(code, timeout=5.0)
    # stdout may have error text before the actual data line — find the line with commas
    data_line = None
    for line in stdout.strip().split('\n'):
        line = line.strip()
        if ',' in line and 'Error' not in line:
            data_line = line
            break
    if not data_line:
        return None
    parts = data_line.split(',', 2)
    try:
        return {
            'version': parts[0].strip() if len(parts) > 0 else '?',
            'freq_mhz': int(parts[1]) // 1_000_000 if len(parts) > 1 else 0,
            'mem_free': int(parts[2]) if len(parts) > 2 else 0,
        }
    except (ValueError, IndexError):
        return None


def _getch():
    """Read a single keypress with arrow key support."""
    if not sys.stdin.isatty():
        ch = sys.stdin.read(1)
        return ch if ch else 'q'
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b'\x1b':
            if select.select([fd], [], [], 0.1)[0]:
                ch2 = os.read(fd, 1)
                if ch2 == b'[':
                    if select.select([fd], [], [], 0.1)[0]:
                        ch3 = os.read(fd, 1)
                        if ch3 == b'A':
                            return 'up'
                        if ch3 == b'B':
                            return 'down'
                        while select.select([fd], [], [], 0.05)[0]:
                            os.read(fd, 1)
            return None
        if ch == b'\x03':
            return 'q'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch.decode('utf-8', errors='replace')


def _getch_nb():
    """Non-blocking getch — returns None immediately if no key waiting."""
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    if not select.select([fd], [], [], 0)[0]:
        return None
    return _getch()


# ── Diagnostic TUI ─────────────────────────────────────────

class DiagTUI:
    """Rich terminal UI for Pico diagnostics."""

    def __init__(self, port=None):
        self.link = PicoLink(port)
        self.console = Console()

        # State
        self.sysinfo = None
        self.firmware_deployed = False
        self.diag_loaded = False
        self.fw_version = None    # config.VERSION on Pico
        self.diag_version = None  # pico_diag.DIAG_VERSION on Pico

        # Results storage: name -> {status, output_lines, elapsed}
        self.test_results = {}

        # Selection state for "run selected" — set of test keys
        self.selected = set()

    # ── Connection ──

    def do_connect(self):
        try:
            self.link.connect()
        except Exception as e:
            return False

        # Tame the hardware watchdog — main.py starts a 5s WDT that can't be
        # stopped on RP2040.  We re-init with max timeout and use a hardware
        # Timer IRQ to feed it continuously.  Timer IRQs run independent of
        # both cores, so they keep firing even during tight loops.
        try:
            self.link.exec_raw(
                "from machine import WDT, Timer\n"
                "try:\n"
                " _wdt=WDT(timeout=8300)\n"
                " _wdt.feed()\n"
                "except:\n pass\n"
                "_wdt_tmr=Timer()\n"
                "_wdt_tmr.init(period=2000,mode=Timer.PERIODIC,"
                "callback=lambda t:_wdt.feed())",
                timeout=5.0,
            )
        except Exception:
            pass  # no WDT active — fine

        # Stop main.py's LED Timer — it survives Ctrl-C into raw REPL.
        # Also stop any legacy Core 1 thread if running old firmware.
        try:
            self.link.exec_raw(
                "from machine import Timer\n"
                "try:\n Timer(-1).deinit()\nexcept:\n pass\n"
                "try:\n _core1_exit=True\nexcept:\n pass",
                timeout=2.0,
            )
        except Exception:
            pass

        try:
            self.sysinfo = get_sysinfo(self.link)
        except Exception:
            pass

        # Free RAM before version checks
        try:
            self.link.exec_raw("import gc\ngc.collect()", timeout=3.0)
        except Exception:
            pass

        # Check firmware and diag versions on Pico
        try:
            stdout, _ = self.link.exec_raw(
                "try:\n import config\n print(config.VERSION)\nexcept:\n print('?')",
                timeout=5.0,
            )
            self.fw_version = stdout.strip() if stdout.strip() != '?' else None
        except Exception:
            self.fw_version = None

        # Read diag version without importing the whole module (avoids MemoryError).
        # Parse DIAG_VERSION = "x.y.z" from the file directly.
        try:
            stdout, _ = self.link.exec_raw(
                "try:\n f=open('pico_diag/__init__.py','r')\n"
                " while True:\n"
                "  l=f.readline()\n"
                "  if not l:break\n"
                "  if l.startswith('DIAG_VERSION'):\n"
                "   print(l.split('\"')[1])\n"
                "   break\n"
                " f.close()\n"
                "except:\n print('?')",
                timeout=5.0,
            )
            v = stdout.strip()
            if v and v != '?' and v != '':
                self.diag_version = v
                self.firmware_deployed = True
            else:
                self.diag_version = None
                self.firmware_deployed = False
        except Exception:
            self.diag_version = None
            self.firmware_deployed = False

        return True

    def disconnect(self):
        self.link.close()
        self.sysinfo = None
        self.firmware_deployed = False
        self.diag_loaded = False

    def load_diag_module(self):
        """Import pico_diag on the Pico (one-time per session)."""
        if self.diag_loaded:
            return True
        if not self.link.connected:
            return False
        try:
            stdout, stderr = self.link.exec_raw(
                "import gc\ngc.collect()\n"
                "print('pre', gc.mem_free())\n"
                "import pico_diag\n"
                "gc.collect()\n"
                "print('ok', gc.mem_free())",
                timeout=20.0,
            )
            if 'ok' in stdout:
                self.diag_loaded = True
                return True
            self._load_error = (stderr or stdout).strip()
            return False
        except Exception as e:
            self._load_error = str(e)
            return False

    # ── Version status line ──

    def version_line(self):
        """One-line version summary for headers."""
        parts = []
        if self.fw_version:
            ok = self.fw_version == EXPECTED_FW_VERSION
            c = "green" if ok else "yellow"
            parts.append(f"fw [bold {c}]v{self.fw_version}[/bold {c}]")
        if self.diag_version:
            ok = self.diag_version == EXPECTED_DIAG_VERSION
            c = "green" if ok else "yellow"
            parts.append(f"diag [bold {c}]v{self.diag_version}[/bold {c}]")
        parts.append(f"[dim]tui v{TUI_VERSION}[/dim]")
        return "  ".join(parts)


# ── Page: Main Menu ────────────────────────────────────────

def render_menu(tui):
    """Render the main menu panel."""
    lines = []

    # System info
    lines.append("[bold]SYSTEM[/bold]")
    if tui.link.connected:
        lines.append(f"  Board     [green]\u25cf[/green] Connected  {tui.link.port}")
    else:
        lines.append("  Board     [red]\u25cf[/red] Disconnected")

    if tui.sysinfo:
        ver = tui.sysinfo['version']
        if len(ver) > 45:
            ver = ver[:45] + "..."
        lines.append(f"  MicroPy   {ver}")
        lines.append(
            f"  CPU       {tui.sysinfo['freq_mhz']} MHz    "
            f"RAM free  {tui.sysinfo['mem_free']:,} bytes"
        )
    else:
        lines.append("  MicroPy   [dim]--[/dim]")

    # Firmware version
    if tui.fw_version:
        if tui.fw_version == EXPECTED_FW_VERSION:
            lines.append(f"  Firmware  [green]\u25cf[/green] v{tui.fw_version}")
        else:
            lines.append(
                f"  Firmware  [yellow]\u25cf[/yellow] v{tui.fw_version}  "
                f"[yellow]expected v{EXPECTED_FW_VERSION} \u2014 redeploy[/yellow]"
            )
    else:
        lines.append("  Firmware  [dim]\u25cf[/dim] --")

    # Diag version
    if tui.firmware_deployed and tui.diag_version:
        if tui.diag_version == EXPECTED_DIAG_VERSION:
            lines.append(f"  Diag      [green]\u25cf[/green] v{tui.diag_version}")
        else:
            lines.append(
                f"  Diag      [yellow]\u25cf[/yellow] v{tui.diag_version}  "
                f"[yellow]expected v{EXPECTED_DIAG_VERSION} \u2014 redeploy[/yellow]"
            )
    else:
        lines.append(
            "  Diag      [yellow]\u25cf[/yellow] Not deployed  "
            "[dim]run: cd tools/ground-station && pnpm deploy:pico[/dim]"
        )

    lines.append(f"  [dim]TUI v{TUI_VERSION}[/dim]")
    lines.append("")

    # Test grid — use Rich Table for proper column alignment with markup
    lines.append("[bold]DIAGNOSTIC TESTS[/bold]")
    lines.append("")  # spacer before table

    body = "\n".join(lines)

    # Build a Table for the test grid
    grid = Table.grid(padding=(0, 2))
    grid.add_column(width=38)  # left test
    grid.add_column(width=38)  # right test

    def _test_cell(t):
        sel = t['key'] in tui.selected
        check = "[bold green]\u25a3[/bold green]" if sel else "[dim]\u25a1[/dim]"
        result = tui.test_results.get(t['name'])
        if result:
            elapsed = result.get('elapsed')
            time_str = f" {_fmt_time(elapsed)}" if elapsed else ""
            if result['status'] == 'pass':
                status = f"[green]PASS{time_str}[/green]"
            elif result['status'] == 'warn':
                status = f"[yellow]WARN{time_str}[/yellow]"
            else:
                status = f"[red]FAIL{time_str}[/red]"
        else:
            extra = "" if t['needs_hw'] else " [dim]noHW[/dim]"
            status = f"[dim]~{_fmt_time(t['est_seconds'])}{extra}[/dim]"
        return f"{check} [bold cyan]\\[{t['key']}][/bold cyan] {t['name']}  {status}"

    half = (len(DIAG_TESTS) + 1) // 2
    for row in range(half):
        left_cell = _test_cell(DIAG_TESTS[row])
        right_idx = row + half
        right_cell = _test_cell(DIAG_TESTS[right_idx]) if right_idx < len(DIAG_TESTS) else ""
        grid.add_row(left_cell, right_cell)

    # Status + controls below the table
    footer_lines = []

    passed = sum(1 for r in tui.test_results.values() if r['status'] == 'pass')
    warned = sum(1 for r in tui.test_results.values() if r['status'] == 'warn')
    failed = sum(1 for r in tui.test_results.values() if r['status'] == 'fail')
    total_results = len(tui.test_results)
    if total_results > 0:
        parts = []
        if passed:
            parts.append(f"[green]{passed} passed[/green]")
        if warned:
            parts.append(f"[yellow]{warned} warn[/yellow]")
        if failed:
            parts.append(f"[red]{failed} failed[/red]")
        footer_lines.append(f"  {', '.join(parts)}")
        footer_lines.append("")

    total_est = sum(t['est_seconds'] for t in DIAG_TESTS)
    footer_lines.append(f"  [bold]\\[1-8][/bold] toggle select    [bold]\\[V][/bold] view test    [bold]\\[A][/bold] run all [dim](~{_fmt_time(total_est)})[/dim]")
    line2_parts = []
    if tui.selected:
        sel_tests = [t for t in DIAG_TESTS if t['key'] in tui.selected]
        sel_est = sum(t['est_seconds'] for t in sel_tests)
        line2_parts.append(f"[bold]\\[R][/bold] run {len(sel_tests)} selected [dim](~{_fmt_time(sel_est)})[/dim]")
        line2_parts.append("[bold]\\[C][/bold] clear")
    if total_results > 0:
        line2_parts.append("[bold]\\[E][/bold] export report")
    line2_parts.append("[bold]\\[Q][/bold] quit")
    footer_lines.append(f"  {'    '.join(line2_parts)}")
    footer = "\n".join(footer_lines)

    # Compose the panel content as a vertical group
    from rich.console import Group
    content = Group(body, grid, footer)

    return Panel(
        content,
        title="[bold white] UNSW ROCKETRY \u2014 PICO DIAGNOSTICS [/bold white]",
        border_style="blue",
        width=90,
        padding=(1, 2),
    )


# ── Page: Test Detail ──────────────────────────────────────

def render_test_detail(tui, test):
    """Render the test detail/preview page."""
    name = test['name']
    result = tui.test_results.get(name)
    lines = []

    # Header info
    hw = "[green]yes[/green]" if test['needs_hw'] else "[dim]no (pure math)[/dim]"
    lines.append(f"[bold]Hardware required:[/bold]  {hw}")
    lines.append(f"[bold]Estimated time:[/bold]    ~{_fmt_time(test['est_seconds'])}")
    lines.append(f"[bold]Timeout:[/bold]           {test['timeout']}s")
    lines.append("")

    # About
    lines.append("[bold]ABOUT[/bold]")
    for line in test['about']:
        lines.append(f"  {line}")
    lines.append("")

    # What it tests
    lines.append("[bold]TESTS FOR[/bold]")
    for item in test['tests_for']:
        lines.append(f"  \u2022 {item}")
    lines.append("")

    # Previous result
    if result:
        elapsed = result.get('elapsed', 0)
        if result['status'] == 'pass':
            status_str = f"[bold green]PASSED[/bold green] in {_fmt_time(elapsed)}"
        elif result['status'] == 'warn':
            status_str = f"[bold yellow]WARNING[/bold yellow] in {_fmt_time(elapsed)}"
        else:
            status_str = f"[bold red]FAILED[/bold red] in {_fmt_time(elapsed)}"
        lines.append(f"[bold]LAST RUN[/bold]  {status_str}")
        lines.append("")

    # Controls
    ctrl_parts = ["[bold][R][/bold] Run test"]
    if result:
        ctrl_parts.append("[bold][L][/bold] View log")
        ctrl_parts.append("[bold][E][/bold] Export log")
    ctrl_parts.append("[bold][B][/bold] Back")
    lines.append("  " + "    ".join(ctrl_parts))

    body = "\n".join(lines)
    return Panel(
        body,
        title=f"[bold white] {name} [/bold white]",
        subtitle=f"[dim]{tui.version_line()}[/dim]",
        border_style="cyan",
        width=90,
        padding=(1, 2),
    )


def render_test_result(tui, test, scroll_offset=0):
    """Render the test result log view."""
    name = test['name']
    result = tui.test_results.get(name)
    if not result:
        return Panel("[dim]No results[/dim]", title=f"[bold] {name} [/bold]", width=90)

    elapsed = result.get('elapsed', 0)
    output_lines = result.get('output_lines', [])

    # Status header
    if result['status'] == 'pass':
        status_str = f"[bold green]\u2713 PASSED[/bold green] in {_fmt_time(elapsed)}"
    elif result['status'] == 'warn':
        status_str = f"[bold yellow]\u26a0 WARNING[/bold yellow] in {_fmt_time(elapsed)}"
    else:
        status_str = f"[bold red]\u2717 FAILED[/bold red] in {_fmt_time(elapsed)}"

    # Build output
    term_h = Console().size.height
    visible_lines = max(5, term_h - 10)
    total = len(output_lines)
    end = min(total, scroll_offset + visible_lines)

    content = Text()
    content.append_text(Text.from_ansi(f"  {strip_ansi(status_str)}\n\n"))
    for line in output_lines[scroll_offset:end]:
        content.append_text(Text.from_ansi(line + "\n"))

    # Scroll indicator
    scroll_info = ""
    if total > visible_lines:
        scroll_info = f"  [dim]lines {scroll_offset + 1}-{end} of {total}  |  \u2191/\u2193 scroll[/dim]"

    lines = []
    lines.append(f"  {status_str}")
    lines.append("")
    for line in output_lines[scroll_offset:end]:
        rich = Text.from_ansi(line)
        lines.append(rich)
    if scroll_info:
        lines.append("")
        lines.append(scroll_info)
    lines.append("")
    lines.append(
        "  [bold][R][/bold] Re-run    "
        "[bold][E][/bold] Export log    "
        "[bold][B][/bold] Back"
    )

    # Build panel content manually with Text objects
    body = Text()
    for item in lines:
        if isinstance(item, Text):
            body.append_text(item)
            body.append("\n")
        else:
            body.append_text(Text.from_markup(item))
            body.append("\n")

    return Panel(
        body,
        title=f"[bold white] {name} \u2014 Results [/bold white]",
        subtitle=f"[dim]{tui.version_line()}[/dim]",
        border_style="green" if result['status'] == 'pass' else (
            "yellow" if result['status'] == 'warn' else "red"
        ),
        width=90,
        padding=(1, 2),
    )


def export_log(tui, test, console):
    """Export test log to a text file."""
    name = test['name']
    result = tui.test_results.get(name)
    if not result:
        return None

    safe_name = name.lower().replace(' ', '_').replace('-', '_')
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    basename = f"diag_{safe_name}_{timestamp}.txt"

    output_lines = result.get('output_lines', [])
    elapsed = result.get('elapsed', 0)
    status = result['status'].upper()

    # Write to both repo docs/ and Desktop
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    desktop = os.path.expanduser("~/Desktop")
    docs_dir = os.path.join(repo_root, "docs", "diagnostics")
    os.makedirs(docs_dir, exist_ok=True)

    paths = [os.path.join(docs_dir, basename), os.path.join(desktop, basename)]

    for path in paths:
        with open(path, 'w') as f:
            f.write(f"MPR Altitude Logger — Pico Diagnostic Report\n")
            f.write(f"Test: {name}\n")
            f.write(f"Status: {status}\n")
            f.write(f"Elapsed: {_fmt_time(elapsed)}\n")
            f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if tui.fw_version:
                f.write(f"Firmware: v{tui.fw_version}\n")
            if tui.diag_version:
                f.write(f"Diag: v{tui.diag_version}\n")
            f.write(f"TUI: v{TUI_VERSION}\n")
            f.write(f"\n{'=' * 60}\n\n")
            for line in output_lines:
                f.write(strip_ansi(line) + "\n")

    return paths


def export_all(tui):
    """Export all test results to a single timestamped text file."""
    if not tui.test_results:
        return None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    basename = f"diag_report_{timestamp}.txt"

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    desktop = os.path.expanduser("~/Desktop")
    docs_dir = os.path.join(repo_root, "docs", "diagnostics")
    os.makedirs(docs_dir, exist_ok=True)

    paths = [os.path.join(docs_dir, basename), os.path.join(desktop, basename)]

    passed = sum(1 for r in tui.test_results.values() if r['status'] == 'pass')
    warned = sum(1 for r in tui.test_results.values() if r['status'] == 'warn')
    failed = sum(1 for r in tui.test_results.values() if r['status'] == 'fail')

    for path in paths:
        with open(path, 'w') as f:
            f.write("MPR Altitude Logger — Pico Diagnostic Report\n")
            f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if tui.fw_version:
                f.write(f"Firmware: v{tui.fw_version}\n")
            if tui.diag_version:
                f.write(f"Diag: v{tui.diag_version}\n")
            f.write(f"TUI: v{TUI_VERSION}\n")
            if tui.sysinfo:
                f.write(f"MicroPython: {tui.sysinfo['version']}\n")
                f.write(f"CPU: {tui.sysinfo['freq_mhz']} MHz\n")
                f.write(f"RAM free: {tui.sysinfo['mem_free']:,} bytes\n")
            f.write(f"\nSummary: {passed} passed, {warned} warn, {failed} failed\n")
            f.write(f"{'=' * 60}\n")

            for t in DIAG_TESTS:
                result = tui.test_results.get(t['name'])
                if not result:
                    continue
                elapsed = result.get('elapsed', 0)
                status = result['status'].upper()
                f.write(f"\n{'─' * 60}\n")
                f.write(f"{t['name']}  —  {status}  ({_fmt_time(elapsed)})\n")
                f.write(f"{'─' * 60}\n\n")
                for line in result.get('output_lines', []):
                    f.write(strip_ansi(line) + "\n")

    return paths


# ── Page: Running Test ──────────────────────────────────────

def run_test(tui, test, console):
    """Run a test with live streaming output. Returns True if completed."""
    name = test['name']
    func = test['func']
    timeout = test['timeout']
    est = test['est_seconds']

    if not tui.link.connected:
        console.print("  [red]Not connected[/red]")
        time.sleep(1)
        return False

    if not tui.load_diag_module():
        err = getattr(tui, '_load_error', '')
        console.print(f"  [red]Failed to load pico_diag: {err}[/red]")
        console.print("  [dim]Try: cd tools/ground-station && pnpm deploy:pico[/dim]")
        time.sleep(1.5)
        return False

    _clear_screen()

    code = f"pico_diag.{func}()"
    lines = []
    has_fail = False
    has_warn = False

    # Header
    console.print(f"\n  [bold yellow]Running:[/bold yellow] {name}")
    console.print(f"  [dim]{test['about'][0]}[/dim]")
    console.print(f"  [dim]Est: ~{_fmt_time(est)}  |  Timeout: {timeout}s  |  Ctrl+C to abort[/dim]")
    console.print(f"  [dim]\u23f1 0:00 / ~{_fmt_time(est)}  (0%)[/dim]")
    console.print()

    start_time = time.monotonic()
    last_timer_update = 0

    try:
        for line in tui.link.exec_streaming(code, timeout=timeout):
            # Update timer on line 4 (fires on real lines AND None heartbeats)
            elapsed = time.monotonic() - start_time
            now_sec = int(elapsed)
            if now_sec > last_timer_update:
                last_timer_update = now_sec
                pct = min(100, (elapsed / est) * 100) if est > 0 else 0
                timer_text = f"  \u23f1 {_fmt_time(elapsed)} / ~{_fmt_time(est)}  ({pct:.0f}%)"
                sys.stdout.write(f"\0337\033[5;1H\033[2K\033[2m{timer_text}\033[0m\0338")
                sys.stdout.flush()

            # None = idle heartbeat, skip line processing
            if line is None:
                continue

            clean = strip_ansi(line)
            lines.append(line)
            if '[FAIL]' in clean:
                has_fail = True
            if '[WARN]' in clean:
                has_warn = True

            console.print(Text.from_ansi(line))
    except KeyboardInterrupt:
        console.print("\n  [yellow]Aborted by user[/yellow]")
        try:
            tui.link.ser.write(b'\x03')
            time.sleep(0.5)
            tui.link._drain()
            tui.link.ser.write(b'\x01')
            time.sleep(0.3)
            tui.link._drain()
            tui.diag_loaded = False
        except Exception:
            pass

    elapsed = time.monotonic() - start_time

    if has_fail:
        status = 'fail'
    elif has_warn:
        status = 'warn'
    else:
        status = 'pass'

    tui.test_results[name] = {
        'status': status,
        'output_lines': lines,
        'elapsed': elapsed,
    }

    return True


# ── Page flows ──────────────────────────────────────────────

def page_test_detail(tui, test, console):
    """Test detail page — shows info, allows run, shows results after."""
    name = test['name']

    while True:
        result = tui.test_results.get(name)

        # Show detail (preview) or result page
        _clear_screen()
        if result:
            console.print(render_test_result(tui, test))
        else:
            console.print(render_test_detail(tui, test))

        key = _getch()
        if key is None:
            continue
        key = key.lower()

        if key == 'b':
            return
        elif key == 'q':
            tui.disconnect()
            sys.exit(0)
        elif key == 'r':
            run_test(tui, test, console)
            # After run, loop back to show result page
            continue
        elif key == 'l' and result:
            # View log with scrolling
            page_log_viewer(tui, test, console)
        elif key == 'e' and result:
            paths = export_log(tui, test, console)
            if paths:
                _clear_screen()
                console.print(f"\n  [green]Exported to:[/green]")
                for p in paths:
                    console.print(f"    {p}")
                console.print()
                console.print("  [dim]Press any key to continue...[/dim]")
                _getch()


def page_log_viewer(tui, test, console):
    """Scrollable log viewer in alternate screen buffer."""
    name = test['name']
    result = tui.test_results.get(name)
    if not result:
        return

    output_lines = result.get('output_lines', [])
    scroll = 0
    term_h = console.size.height
    visible = max(5, term_h - 6)

    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        while True:
            total = len(output_lines)
            max_scroll = max(0, total - visible)
            scroll = max(0, min(scroll, max_scroll))

            end = min(total, scroll + visible)

            # Render
            body_lines = []
            for line in output_lines[scroll:end]:
                body_lines.append(Text.from_ansi(line))

            if total > visible:
                pct = int((scroll / max_scroll) * 100) if max_scroll > 0 else 0
                body_lines.append(Text.from_markup(
                    f"\n  [dim]lines {scroll + 1}-{end} of {total}  ({pct}%)  |  "
                    f"\u2191/\u2193 scroll  |  B: back[/dim]"
                ))
            else:
                body_lines.append(Text.from_markup("\n  [dim]B: back[/dim]"))

            body = Text()
            for bl in body_lines:
                body.append_text(bl)
                body.append("\n")

            elapsed = result.get('elapsed', 0)
            if result['status'] == 'pass':
                border = "green"
                tag = f"PASSED {_fmt_time(elapsed)}"
            elif result['status'] == 'warn':
                border = "yellow"
                tag = f"WARNING {_fmt_time(elapsed)}"
            else:
                border = "red"
                tag = f"FAILED {_fmt_time(elapsed)}"

            panel = Panel(
                body,
                title=f"[bold] {name} \u2014 {tag} [/bold]",
                border_style=border,
                width=min(110, console.size.width - 2),
                padding=(0, 1),
            )

            with console.capture() as cap:
                console.print(panel)
            rendered = cap.get().rstrip("\n")
            sys.stdout.write("\033[H\033[?25l" + rendered + "\033[J")
            sys.stdout.flush()

            key = _getch()
            if key == 'b':
                return
            if key == 'q':
                sys.exit(0)
            if key in ('up', 'k', 'w'):
                scroll = max(0, scroll - 3)
            elif key in ('down', 'j', 's'):
                scroll = min(max_scroll, scroll + 3)
            elif key is None:
                continue
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def browse_diag_results(tui, console, tests):
    """Interactive two-column results browser (matches simulator style)."""
    from rich.layout import Layout

    items = []
    for t in tests:
        result = tui.test_results.get(t['name'])
        if result:
            items.append((t, result))

    if not items:
        return

    cursor = 0
    export_msg = None

    passed = sum(1 for _, r in items if r['status'] == 'pass')
    warned = sum(1 for _, r in items if r['status'] == 'warn')
    failed = sum(1 for _, r in items if r['status'] == 'fail')

    def render_test_list(max_lines):
        lines = []
        for i, (t, result) in enumerate(items):
            elapsed = result.get('elapsed', 0)
            if result['status'] == 'pass':
                status = "[green]PASS[/green]"
            elif result['status'] == 'warn':
                status = "[yellow]WARN[/yellow]"
            else:
                status = "[red]FAIL[/red]"
            marker = "\u25b8" if i == cursor else " "
            if i == cursor:
                lines.append(f"  [bold]{marker} {status}  {t['name']:<22s} {_fmt_time(elapsed)}[/bold]")
            else:
                lines.append(f"  {marker} {status}  [dim]{t['name']:<22s} {_fmt_time(elapsed)}[/dim]")

        visible = lines[:max_lines]
        return "\n".join(visible) if visible else "[dim]No results[/dim]"

    def render_detail(max_lines):
        t, result = items[cursor]
        elapsed = result.get('elapsed', 0)
        output_lines = result.get('output_lines', [])
        lines = []

        # Status badge
        if result['status'] == 'pass':
            lines.append("[bold green]\u2713 PASSED[/bold green]")
        elif result['status'] == 'warn':
            lines.append("[bold yellow]\u26a0 WARNING[/bold yellow]")
        else:
            lines.append("[bold red]\u2717 FAILED[/bold red]")
        lines.append("")

        # Test info
        lines.append(f"[bold]{t['name']}[/bold]")
        hw = "[green]yes[/green]" if t['needs_hw'] else "[dim]no (pure math)[/dim]"
        lines.append(f"  Hardware: {hw}    Duration: {_fmt_time(elapsed)}")
        lines.append("")

        # About
        lines.append("[bold]About[/bold]")
        for line in t['about']:
            lines.append(f"  {line}")
        lines.append("")

        # Tests for (checklist)
        lines.append("[bold]Tests For[/bold]")
        for item in t['tests_for']:
            if result['status'] == 'pass':
                lines.append(f"  [green]\u2713[/green] {item}")
            elif result['status'] == 'warn':
                lines.append(f"  [yellow]\u26a0[/yellow] {item}")
            else:
                lines.append(f"  [red]\u2717[/red] {item}")
        lines.append("")

        # Output log (truncated to fit)
        if output_lines:
            lines.append("[bold]Output[/bold]")
            remaining = max_lines - len(lines) - 1
            shown = output_lines[:remaining] if remaining > 0 else []
            for ol in shown:
                lines.append(f"  {ol}")
            if len(output_lines) > remaining > 0:
                lines.append(f"  [dim]... {len(output_lines) - remaining} more lines[/dim]")

        return "\n".join(
            str(Text.from_ansi(l)) if '\x1b' in str(l) else str(l)
            for l in lines[:max_lines]
        ) if lines else ""

    def draw():
        term_h = console.size.height
        max_lines = max(5, term_h - 3)

        summary_parts = []
        if passed:
            summary_parts.append(f"[green]{passed} passed[/green]")
        if warned:
            summary_parts.append(f"[yellow]{warned} warn[/yellow]")
        if failed:
            summary_parts.append(f"[red]{failed} failed[/red]")
        summary = ", ".join(summary_parts)
        if export_msg:
            bar = f"  {summary}  \u2502  [bold green]{export_msg}[/bold green]"
        else:
            bar = f"  {summary}  \u2502  \u2191/\u2193: navigate  \u2502  E: export  \u2502  B: back  \u2502  Q: quit"

        layout = Layout(size=term_h)
        layout.split_column(
            Layout(name="main"),
            Layout(bar, name="bar", size=1),
        )
        layout["main"].split_row(
            Layout(Panel(
                render_test_list(max_lines),
                title="[bold] Results [/bold]",
                border_style="blue",
                padding=(0, 1),
            ), name="list"),
            Layout(Panel(
                render_detail(max_lines),
                title="[bold] Detail [/bold]",
                border_style="cyan",
                padding=(0, 1),
            ), name="detail", ratio=1),
        )
        layout["main"]["list"].ratio = 1

        with console.capture() as cap:
            console.print(layout)
        output = cap.get().rstrip("\n")
        sys.stdout.write("\033[H\033[?25l" + output + "\033[J")
        sys.stdout.flush()

    # Enter alternate screen buffer
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        draw()
        while True:
            key = _getch()
            export_msg = None
            if key in ('up', 'k'):
                cursor = max(0, cursor - 1)
            elif key in ('down', 'j'):
                cursor = min(len(items) - 1, cursor + 1)
            elif key == 'e':
                paths = export_all(tui)
                if paths:
                    export_msg = f"Exported to {os.path.basename(paths[1])}"
            elif key == 'b':
                return
            elif key in ('q',):
                tui.disconnect()
                sys.exit(0)
            elif key is None:
                continue
            else:
                continue
            draw()
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def page_run_all(tui, console, tests=None):
    """Run tests in two-column live view, then transition to interactive browser."""
    if tests is None:
        tests = DIAG_TESTS

    from rich.layout import Layout

    current_idx = -1
    is_running = False
    current_output = []
    timer_text = ""
    last_draw_t = 0

    def draw():
        nonlocal last_draw_t
        last_draw_t = time.monotonic()
        term_h = console.size.height
        max_lines = max(5, term_h - 3)

        # ── Left panel: test list ──
        list_lines = []
        for i, t in enumerate(tests):
            result = tui.test_results.get(t['name'])
            if result:
                elapsed = _fmt_time(result.get('elapsed', 0))
                if result['status'] == 'pass':
                    status = "[green]PASS[/green]"
                elif result['status'] == 'warn':
                    status = "[yellow]WARN[/yellow]"
                else:
                    status = "[red]FAIL[/red]"
                list_lines.append(f"  {status}  {t['name']:<22s} {elapsed}")
            elif i == current_idx and is_running:
                spin = spinner_char()
                list_lines.append(
                    f"  [bold yellow]{spin}[/bold yellow]     [bold]{t['name']}[/bold]"
                )
            else:
                list_lines.append(
                    f"  [dim]\u00b7     {t['name']:<22s} ~{_fmt_time(t['est_seconds'])}[/dim]"
                )
        list_content = "\n".join(list_lines)

        # ── Right panel: live output or last result ──
        if is_running:
            visible = current_output[-(max_lines):]
            if visible:
                detail_body = Text()
                for line in visible:
                    detail_body.append_text(Text.from_ansi(line))
                    detail_body.append("\n")
            else:
                detail_body = "[dim]Starting...[/dim]"
            detail_title = f"[bold] {tests[current_idx]['name']} [/bold]"
            detail_border = "yellow"
        elif current_idx >= 0 and current_idx < len(tests):
            result = tui.test_results.get(tests[current_idx]['name'])
            if result:
                out = result.get('output_lines', [])
                visible = out[-(max_lines):]
                detail_body = Text()
                for line in visible:
                    detail_body.append_text(Text.from_ansi(line))
                    detail_body.append("\n")
                detail_title = f"[bold] {tests[current_idx]['name']} [/bold]"
                detail_border = (
                    "green" if result['status'] == 'pass'
                    else "yellow" if result['status'] == 'warn'
                    else "red"
                )
            else:
                detail_body = ""
                detail_title = "[bold] Detail [/bold]"
                detail_border = "cyan"
        else:
            detail_body = "[dim]Waiting...[/dim]"
            detail_title = "[bold] Detail [/bold]"
            detail_border = "cyan"

        # ── Bottom bar ──
        completed = sum(1 for t in tests if tui.test_results.get(t['name']))
        p = sum(1 for t in tests if (r := tui.test_results.get(t['name'])) and r['status'] == 'pass')
        w = sum(1 for t in tests if (r := tui.test_results.get(t['name'])) and r['status'] == 'warn')
        f = sum(1 for t in tests if (r := tui.test_results.get(t['name'])) and r['status'] == 'fail')
        parts = [f"{completed}/{len(tests)} tests"]
        if p:
            parts.append(f"[green]{p} passed[/green]")
        if w:
            parts.append(f"[yellow]{w} warn[/yellow]")
        if f:
            parts.append(f"[red]{f} failed[/red]")
        if timer_text:
            parts.append(timer_text)
        bar = "  " + "  \u2502  ".join(parts)

        # ── Layout ──
        layout = Layout(size=term_h)
        layout.split_column(
            Layout(name="main"),
            Layout(bar, name="bar", size=1),
        )
        layout["main"].split_row(
            Layout(Panel(
                list_content,
                title="[bold] Diagnostics [/bold]",
                border_style="blue",
                padding=(0, 1),
            ), name="list"),
            Layout(Panel(
                detail_body,
                title=detail_title,
                border_style=detail_border,
                padding=(0, 1),
            ), name="detail", ratio=2),
        )
        layout["main"]["list"].ratio = 1

        with console.capture() as cap:
            console.print(layout)
        output = cap.get().rstrip("\n")
        sys.stdout.write("\033[H" + output + "\033[J")
        sys.stdout.flush()

    # Enter alternate screen
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        draw()

        # Load diag module
        if not tui.load_diag_module():
            err = getattr(tui, '_load_error', '')
            current_output.append(f"Failed to load pico_diag: {err}")
            current_output.append("Try: cd tools/ground-station && pnpm deploy:pico")
            draw()
            time.sleep(3)
            return

        for idx, test in enumerate(tests):
            current_idx = idx
            is_running = True
            current_output.clear()
            draw()

            start = time.monotonic()
            code = f"pico_diag.{test['func']}()"
            has_fail = False
            has_warn = False

            try:
                for line in tui.link.exec_streaming(code, timeout=test['timeout']):
                    elapsed = time.monotonic() - start
                    est = test['est_seconds']
                    pct = min(100, (elapsed / est) * 100) if est > 0 else 0
                    timer_text = (
                        f"\u23f1 {_fmt_time(elapsed)} / ~{_fmt_time(est)}  ({pct:.0f}%)"
                    )

                    if line is None:
                        # Heartbeat — throttle redraws to ~5 fps
                        if time.monotonic() - last_draw_t > 0.2:
                            draw()
                        continue

                    clean = strip_ansi(line)
                    current_output.append(line)
                    if '[FAIL]' in clean:
                        has_fail = True
                    if '[WARN]' in clean:
                        has_warn = True
                    draw()

            except KeyboardInterrupt:
                current_output.append("[Aborted by user]")
                try:
                    tui.link.ser.write(b'\x03')
                    time.sleep(0.5)
                except Exception:
                    pass

            elapsed = time.monotonic() - start
            status = 'fail' if has_fail else ('warn' if has_warn else 'pass')
            tui.test_results[test['name']] = {
                'status': status,
                'output_lines': list(current_output),
                'elapsed': elapsed,
            }

            is_running = False
            timer_text = ""
            draw()

            if not tui.link.connected:
                break
            time.sleep(0.3)

    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()

    # Transition to interactive results browser
    browse_diag_results(tui, console, tests)


# ── Main ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="UNSW Rocketry - Pico Diagnostic TUI"
    )
    parser.add_argument("--port", help="Serial port (auto-detect if omitted)")
    args = parser.parse_args()

    tui = DiagTUI(port=args.port)
    console = tui.console

    # Connect
    _clear_screen()
    console.print(render_menu(tui))
    with console.status("[bold yellow]Connecting to Pico...", spinner="dots"):
        tui.do_connect()

    while True:
        _clear_screen()
        console.print(render_menu(tui))
        console.print("  [bold]Press a key:[/bold] ", end="")

        try:
            choice = _getch()
        except (KeyboardInterrupt, EOFError):
            console.print()
            break

        if choice is None or choice in ('up', 'down', 'left', 'right'):
            continue

        ch = choice.lower() if isinstance(choice, str) else choice

        # Numbers toggle selection
        test = next((t for t in DIAG_TESTS if t['key'] == ch), None)
        if test:
            if test['key'] in tui.selected:
                tui.selected.discard(test['key'])
            else:
                tui.selected.add(test['key'])
            continue

        if ch == 'q':
            break
        elif ch == 'a':
            page_run_all(tui, console, tests=DIAG_TESTS)
        elif ch == 'r' and tui.selected:
            sel_tests = [t for t in DIAG_TESTS if t['key'] in tui.selected]
            page_run_all(tui, console, tests=sel_tests)
        elif ch == 'c':
            tui.selected.clear()
        elif ch == 'e' and tui.test_results:
            paths = export_all(tui)
            if paths:
                _clear_screen()
                console.print(f"\n  [green]Exported {len(tui.test_results)} test(s) to:[/green]")
                for p in paths:
                    console.print(f"    {p}")
                console.print()
                console.print("  [dim]Press any key to continue...[/dim]")
                _getch()
        elif ch == 'v':
            # Open two-column results browser if any results exist
            if tui.test_results:
                browse_diag_results(tui, console, DIAG_TESTS)
            else:
                console.print("\n  [dim]No results yet — run some tests first[/dim]")
                time.sleep(1)

    tui.disconnect()
    console.print("\n[dim]Disconnected.[/dim]")


if __name__ == "__main__":
    main()
