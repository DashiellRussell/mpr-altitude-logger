#!/usr/bin/env python3
"""
Post-flight analysis TUI for MPR Altitude Logger.

Interactive terminal dashboard for reviewing rocket flight data. Can download
logs directly from a Pico over serial, or analyze .bin files from disk.

Usage:
    python postflight.py                          # download from Pico
    python postflight.py flight.bin               # analyze local file
    python postflight.py flight.bin --sim sim.csv  # compare with simulation
    python postflight.py --download --port /dev/ttyACM0

Dependencies: rich, pyserial (for download only)
"""

import struct
import sys
import os
import csv
import argparse
import time
import select
import tty
import termios
import base64
from pathlib import Path
from io import StringIO

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.layout import Layout
    from rich.live import Live
    from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn
    from rich import box
except ImportError:
    print("Missing dependency: rich")
    print("  pip install rich")
    sys.exit(1)


# ── Binary Log Format ────────────────────────────────────────

FRAME_HEADER = b'\xAA\x55'
FILE_HEADER_SIZE = 10  # 6 magic + 2 version + 2 frame_size

FRAME_FORMAT_V2 = '<IB f f f f f HHH B'
FRAME_SIZE_V2 = struct.calcsize(FRAME_FORMAT_V2)
FIELD_NAMES_V2 = [
    "timestamp_ms", "state", "pressure_pa", "temperature_c",
    "alt_raw_m", "alt_filtered_m", "vel_filtered_ms",
    "v_3v3_mv", "v_5v_mv", "v_9v_mv", "flags"
]

FRAME_FORMAT_V1 = '<IB f f f f f H B'
FRAME_SIZE_V1 = struct.calcsize(FRAME_FORMAT_V1)
FIELD_NAMES_V1 = [
    "timestamp_ms", "state", "pressure_pa", "temperature_c",
    "alt_raw_m", "alt_filtered_m", "vel_filtered_ms",
    "v_batt_mv", "flags"
]

STATE_NAMES = {
    0: "PAD", 1: "BOOST", 2: "COAST", 3: "APOGEE",
    4: "DROGUE", 5: "MAIN", 6: "LANDED"
}

STATE_COLORS = {
    "PAD": "white", "BOOST": "red", "COAST": "yellow",
    "APOGEE": "green", "DROGUE": "cyan", "MAIN": "blue",
    "LANDED": "magenta"
}

FLAG_ARMED = 0x01
FLAG_DROGUE_FIRED = 0x02
FLAG_MAIN_FIRED = 0x04
FLAG_ERROR = 0x08


# ── Log Decoder ──────────────────────────────────────────────

def decode_flags(flags: int) -> list[str]:
    """Decode flags byte into list of flag names."""
    parts = []
    if flags & FLAG_ARMED:
        parts.append("ARMED")
    if flags & FLAG_DROGUE_FIRED:
        parts.append("DROGUE_FIRED")
    if flags & FLAG_MAIN_FIRED:
        parts.append("MAIN_FIRED")
    if flags & FLAG_ERROR:
        parts.append("ERROR")
    return parts


def decode_bin(data: bytes) -> tuple[list[dict], int]:
    """
    Decode binary flight log data.
    Returns (frames, version).
    """
    version = 2
    if data[:6] == b'RKTLOG':
        version, fsize = struct.unpack_from('<HH', data, 6)
        offset = FILE_HEADER_SIZE
    else:
        offset = 0

    if version >= 2:
        fmt, fsize, fields = FRAME_FORMAT_V2, FRAME_SIZE_V2, FIELD_NAMES_V2
    else:
        fmt, fsize, fields = FRAME_FORMAT_V1, FRAME_SIZE_V1, FIELD_NAMES_V1

    frames = []
    while offset < len(data) - (2 + fsize):
        if data[offset:offset + 2] != FRAME_HEADER:
            offset += 1
            continue
        offset += 2
        if offset + fsize > len(data):
            break
        values = struct.unpack_from(fmt, data, offset)
        offset += fsize
        frame = dict(zip(fields, values))
        frame["state_name"] = STATE_NAMES.get(frame["state"], "UNKNOWN")
        frame["flags_list"] = decode_flags(frame["flags"])
        frames.append(frame)

    return frames, version


def decode_file(filepath: str) -> tuple[list[dict], int]:
    """Decode a .bin flight log file."""
    data = Path(filepath).read_bytes()
    return decode_bin(data)


# ── Simulation CSV Loader ────────────────────────────────────

def load_sim_csv(filepath: str) -> list[dict]:
    """
    Load simulation CSV (from openrocket_import.py or simulate.py).
    Expected columns: time_s, altitude_m, velocity_ms, ...
    """
    rows = []
    with open(filepath, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = {}
            for k, v in row.items():
                try:
                    parsed[k.strip()] = float(v)
                except (ValueError, TypeError):
                    parsed[k.strip()] = v
            rows.append(parsed)
    return rows


# ── Flight Analysis ──────────────────────────────────────────

class FlightData:
    """Processed flight data ready for display."""

    def __init__(self, frames: list[dict], version: int):
        self.frames = frames
        self.version = version
        self.n_frames = len(frames)

        if not frames:
            return

        t0 = frames[0]["timestamp_ms"]
        self.times = [(f["timestamp_ms"] - t0) / 1000.0 for f in frames]
        self.altitudes = [f["alt_filtered_m"] for f in frames]
        self.alt_raw = [f["alt_raw_m"] for f in frames]
        self.velocities = [f["vel_filtered_ms"] for f in frames]
        self.pressures = [f["pressure_pa"] for f in frames]
        self.temperatures = [f["temperature_c"] for f in frames]
        self.states = [f["state"] for f in frames]

        # Power rails
        if version >= 2:
            self.v_3v3 = [f["v_3v3_mv"] / 1000.0 for f in frames]
            self.v_5v = [f["v_5v_mv"] / 1000.0 for f in frames]
            self.v_9v = [f["v_9v_mv"] / 1000.0 for f in frames]
        else:
            self.v_batt = [f["v_batt_mv"] / 1000.0 for f in frames]

        # Key metrics
        self.max_alt = max(self.altitudes)
        self.max_alt_idx = self.altitudes.index(self.max_alt)
        self.max_alt_time = self.times[self.max_alt_idx]

        self.max_vel = max(self.velocities)
        self.max_vel_idx = self.velocities.index(self.max_vel)
        self.max_vel_time = self.times[self.max_vel_idx]

        self.duration = self.times[-1]
        self.sample_rate = self.n_frames / self.duration if self.duration > 0 else 0

        # Estimate max acceleration from velocity derivative
        self.max_accel = 0.0
        self.max_accel_time = 0.0
        for i in range(1, len(self.velocities)):
            dt = self.times[i] - self.times[i - 1]
            if dt > 0:
                accel = (self.velocities[i] - self.velocities[i - 1]) / dt
                if accel > self.max_accel:
                    self.max_accel = accel
                    self.max_accel_time = self.times[i]

        # Landing velocity (last few frames average)
        n_land = min(10, len(self.velocities))
        self.landing_vel = sum(self.velocities[-n_land:]) / n_land

        # State transitions
        self.transitions = []
        for i in range(1, len(self.states)):
            if self.states[i] != self.states[i - 1]:
                self.transitions.append({
                    "time": self.times[i],
                    "from_state": STATE_NAMES.get(self.states[i - 1], "?"),
                    "to_state": STATE_NAMES.get(self.states[i], "?"),
                })

        # Deployment events
        self.drogue_fired = False
        self.drogue_time = None
        self.main_fired = False
        self.main_time = None

        for i, f in enumerate(frames):
            if not self.drogue_fired and (f["flags"] & FLAG_DROGUE_FIRED):
                self.drogue_fired = True
                self.drogue_time = self.times[i]
            if not self.main_fired and (f["flags"] & FLAG_MAIN_FIRED):
                self.main_fired = True
                self.main_time = self.times[i]

        # ARM status
        self.was_armed = any(f["flags"] & FLAG_ARMED for f in frames)
        self.had_error = any(f["flags"] & FLAG_ERROR for f in frames)

    def get_transition_time(self, state_name: str) -> float | None:
        """Get time of transition TO a given state."""
        for t in self.transitions:
            if t["to_state"] == state_name:
                return t["time"]
        return None


class SimData:
    """Processed simulation data."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.times = [r.get("time_s", 0) for r in rows]
        self.altitudes = [r.get("altitude_m", 0) for r in rows]
        self.velocities = [r.get("velocity_ms", 0) for r in rows]

        self.max_alt = max(self.altitudes) if self.altitudes else 0
        self.max_alt_idx = self.altitudes.index(self.max_alt) if self.altitudes else 0
        self.max_alt_time = self.times[self.max_alt_idx] if self.times else 0

        self.max_vel = max(self.velocities) if self.velocities else 0
        self.duration = self.times[-1] if self.times else 0


# ── TUI Rendering ────────────────────────────────────────────

SPARKLINE_CHARS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 50) -> str:
    """Generate a sparkline string from values."""
    if not values:
        return ""
    # Downsample
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][:width]

    mn = min(sampled)
    mx = max(sampled)
    rng = mx - mn if mx != mn else 1.0

    chars = []
    for v in sampled:
        idx = int((v - mn) / rng * (len(SPARKLINE_CHARS) - 1))
        idx = max(0, min(len(SPARKLINE_CHARS) - 1, idx))
        chars.append(SPARKLINE_CHARS[idx])
    return "".join(chars)


def render_altitude_chart(
    times: list[float],
    altitudes: list[float],
    width: int = 60,
    height: int = 18,
    sim_times: list[float] | None = None,
    sim_alts: list[float] | None = None,
    transitions: list[dict] | None = None,
) -> str:
    """Render altitude vs time as ASCII column chart."""
    if not times or not altitudes:
        return "(no data)"

    max_alt = max(altitudes) * 1.1
    if sim_alts:
        max_alt = max(max_alt, max(sim_alts) * 1.1)
    max_alt = max(max_alt, 1.0)
    max_t = max(times)
    if max_t <= 0:
        return "(no time data)"

    # Y-axis labels width
    label_w = 7

    lines = []

    for row in range(height, -1, -1):
        threshold = (row / height) * max_alt
        # Y-axis label
        if row % 4 == 0 or row == height:
            label = f"{threshold:5.0f}m"
        else:
            label = " " * 6
        label = label.rjust(label_w - 1) + "|"

        chars = []
        for col in range(width):
            t = (col / width) * max_t
            # Find nearest altitude
            idx = min(range(len(times)), key=lambda i, t=t: abs(times[i] - t))
            alt = altitudes[idx]

            sim_hit = False
            if sim_times and sim_alts:
                sidx = min(range(len(sim_times)), key=lambda i, t=t: abs(sim_times[i] - t))
                if sim_alts[sidx] >= threshold:
                    sim_hit = True

            if alt >= threshold:
                chars.append("[bold blue]\u2588[/bold blue]")
            elif sim_hit:
                chars.append("[dim red]\u2591[/dim red]")
            else:
                chars.append(" ")
        lines.append(label + "".join(chars))

    # X-axis
    x_axis = " " * label_w
    for col in range(width):
        if col % 10 == 0:
            t = (col / width) * max_t
            lbl = f"{t:.0f}s"
            x_axis += lbl
            col_skip = len(lbl)
        elif col_skip > 1:
            col_skip -= 1
        else:
            x_axis += "\u2500"
    lines.append(" " * label_w + "\u2500" * width)
    # Time labels
    time_labels = " " * label_w
    n_labels = min(6, width // 10)
    for i in range(n_labels + 1):
        pos = int(i * width / max(n_labels, 1))
        t = (pos / width) * max_t
        lbl = f"{t:.0f}s"
        while len(time_labels) < label_w + pos:
            time_labels += " "
        time_labels += lbl
    lines.append(time_labels)

    # Event markers
    if transitions:
        event_line = " " * label_w
        for tr in transitions:
            pos = int((tr["time"] / max_t) * width) if max_t > 0 else 0
            pos = min(pos, width - 1)
            while len(event_line) < label_w + pos:
                event_line += " "
            event_line += f"[{STATE_COLORS.get(tr['to_state'], 'white')}]\u25bc{tr['to_state']}[/]"
        lines.append(event_line)

    return "\n".join(lines)


def render_state_timeline(flight: FlightData, width: int = 60) -> Text:
    """Render state timeline as colored bar."""
    if not flight.frames:
        return Text("(no data)")

    text = Text()
    max_t = flight.duration
    if max_t <= 0:
        return Text("(no time data)")

    # Build state segments
    segments = []
    current_state = STATE_NAMES.get(flight.states[0], "?")
    start_t = 0.0

    for tr in flight.transitions:
        segments.append((start_t, tr["time"], current_state))
        current_state = tr["to_state"]
        start_t = tr["time"]
    segments.append((start_t, max_t, current_state))

    # Render
    for start, end, state in segments:
        frac = (end - start) / max_t
        n_chars = max(1, int(frac * width))
        color = STATE_COLORS.get(state, "white")
        text.append(state, style=f"bold {color}")
        text.append(" ")
        text.append("\u2588" * n_chars, style=color)
        text.append(" ")

    # Time labels below
    text.append("\n  ")
    for seg in segments:
        text.append(f"{seg[0]:.1f}s", style="dim")
        n_chars = max(1, int(((seg[1] - seg[0]) / max_t) * width))
        text.append(" " * max(0, n_chars - 4))
    text.append(f"{max_t:.1f}s", style="dim")

    return text


def build_summary_panel(flight: FlightData) -> Panel:
    """Build the flight summary panel."""
    lines = []

    lines.append(f"  [bold]Apogee[/]        [cyan]{flight.max_alt:7.1f} m AGL[/]    @ T+{flight.max_alt_time:.2f}s")
    lines.append(f"  [bold]Max Velocity[/]  [cyan]{flight.max_vel:7.1f} m/s[/]      @ T+{flight.max_vel_time:.2f}s")
    lines.append(f"  [bold]Max Accel[/]     [cyan]~{flight.max_accel:6.1f} m/s\u00b2[/]    (estimated from velocity)")
    lines.append(f"  [bold]Flight Time[/]   [cyan]{flight.duration:7.1f} s[/]        (launch to landing)")
    lines.append(f"  [bold]Sample Rate[/]   [cyan]{flight.sample_rate:7.1f} Hz[/]      ({flight.n_frames:,} frames)")
    lines.append("")

    lines.append(f"  [bold]Landing Vel[/]   [cyan]{flight.landing_vel:7.1f} m/s[/]")
    lines.append("")

    # Status flags
    if flight.had_error:
        lines.append(f"  [red]\u25cf ERROR flag detected[/]")

    content = "\n".join(lines)
    return Panel(content, title="[bold white]FLIGHT SUMMARY[/]", border_style="cyan", padding=(1, 2))


def build_power_panel(flight: FlightData) -> Panel:
    """Build power rails summary."""
    lines = []
    if flight.version >= 2:
        mn3, mx3 = min(flight.v_3v3), max(flight.v_3v3)
        mn5, mx5 = min(flight.v_5v), max(flight.v_5v)
        mn9, mx9 = min(flight.v_9v), max(flight.v_9v)

        ok3 = "green" if mn3 > 3.0 else "red"
        ok5 = "green" if mn5 > 4.5 else "red"
        ok9 = "green" if mn9 > 8.0 else "red"

        lines.append(f"  3V3  [{ok3}]{mn3:.2f}V\u2014{mx3:.2f}V[/]  {'OK' if mn3 > 3.0 else 'LOW'}     "
                      f"5V  [{ok5}]{mn5:.2f}V\u2014{mx5:.2f}V[/]  {'OK' if mn5 > 4.5 else 'LOW'}     "
                      f"9V  [{ok9}]{mn9:.2f}V\u2014{mx9:.2f}V[/]  {'OK' if mn9 > 8.0 else 'LOW'}")
    else:
        mn, mx = min(flight.v_batt), max(flight.v_batt)
        ok = "green" if mn > 3.0 else "red"
        lines.append(f"  Battery  [{ok}]{mn:.2f}V\u2014{mx:.2f}V[/]  {'OK' if mn > 3.0 else 'LOW'}")

    content = "\n".join(lines)
    return Panel(content, title="[bold white]POWER RAILS[/]", border_style="dim", padding=(0, 1))


def build_sim_comparison_panel(flight: FlightData, sim: SimData, cd_old: float = 0.45) -> Panel:
    """Build actual vs predicted comparison."""
    delta_alt = flight.max_alt - sim.max_alt
    delta_t_apo = flight.max_alt_time - sim.max_alt_time
    delta_vel = flight.max_vel - sim.max_vel
    delta_dur = flight.duration - sim.duration

    table = Table(show_header=True, box=box.SIMPLE, padding=(0, 2))
    table.add_column("", style="bold", width=20)
    table.add_column("Actual", justify="right", width=12)
    table.add_column("Predicted", justify="right", width=12)
    table.add_column("Delta", justify="right", width=12)

    d_alt_color = "green" if abs(delta_alt) < 50 else "yellow" if abs(delta_alt) < 100 else "red"
    d_vel_color = "green" if abs(delta_vel) < 20 else "yellow" if abs(delta_vel) < 50 else "red"

    table.add_row("Apogee",
                   f"{flight.max_alt:.1f} m",
                   f"{sim.max_alt:.1f} m",
                   f"[{d_alt_color}]{delta_alt:+.1f} m[/]")
    table.add_row("Time to Apogee",
                   f"{flight.max_alt_time:.1f} s",
                   f"{sim.max_alt_time:.1f} s",
                   f"{delta_t_apo:+.1f} s")
    table.add_row("Max Velocity",
                   f"{flight.max_vel:.1f} m/s",
                   f"{sim.max_vel:.1f} m/s",
                   f"[{d_vel_color}]{delta_vel:+.1f} m/s[/]")
    table.add_row("Flight Duration",
                   f"{flight.duration:.1f} s",
                   f"{sim.duration:.1f} s",
                   f"{delta_dur:+.1f} s")

    # Cd suggestion
    cd_suggestion = ""
    if sim.max_alt > 0 and flight.max_alt > 0:
        cd_new = cd_old * (sim.max_alt / flight.max_alt) ** 0.5
        if abs(cd_new - cd_old) > 0.005:
            direction = "increase" if cd_new > cd_old else "decrease"
            cd_suggestion = f"\n  Suggested Cd adjustment: {direction} from {cd_old:.2f} to ~{cd_new:.3f}"

    content = StringIO()
    temp_console = Console(file=content, width=70, no_color=False)
    temp_console.print(table)
    result = content.getvalue()
    if cd_suggestion:
        result += cd_suggestion

    return Panel(result, title="[bold white]ACTUAL vs PREDICTED[/]", border_style="yellow", padding=(0, 1))


def build_velocity_panel(flight: FlightData) -> Panel:
    """Build velocity sparkline panel."""
    spark = sparkline(flight.velocities, width=55)
    content = f"  Vel  {spark}  [cyan]{flight.max_vel:+.1f} m/s peak[/]"
    return Panel(content, title="[bold white]VELOCITY PROFILE[/]", border_style="dim", padding=(0, 1))


def build_full_dashboard(flight: FlightData, sim: SimData | None = None) -> str:
    """Build the full dashboard as renderable rich objects."""
    console = Console()
    renderables = []

    # Summary
    renderables.append(build_summary_panel(flight))

    # Altitude chart
    sim_t = sim.times if sim else None
    sim_a = sim.altitudes if sim else None
    chart = render_altitude_chart(
        flight.times, flight.altitudes,
        width=60, height=18,
        sim_times=sim_t, sim_alts=sim_a,
        transitions=flight.transitions,
    )
    legend = "[bold blue]\u2588[/] Actual"
    if sim:
        legend += "  [dim red]\u2591[/] Simulated"
    chart_panel = Panel(
        Text.from_markup(chart + f"\n\n  {legend}"),
        title="[bold white]ALTITUDE PROFILE[/]",
        border_style="blue",
        padding=(0, 1),
    )
    renderables.append(chart_panel)

    # State timeline
    timeline = render_state_timeline(flight)
    renderables.append(Panel(timeline, title="[bold white]STATE TIMELINE[/]", border_style="dim", padding=(0, 1)))

    # Velocity sparkline
    renderables.append(build_velocity_panel(flight))

    # Power rails
    renderables.append(build_power_panel(flight))

    # Sim comparison
    if sim:
        renderables.append(build_sim_comparison_panel(flight, sim))

    return renderables


# ── Serial / Pico Download ───────────────────────────────────

def find_pico_port() -> str | None:
    """Auto-detect Pico serial port."""
    import glob
    candidates = (
        glob.glob("/dev/ttyACM*") +
        glob.glob("/dev/tty.usbmodem*") +
        glob.glob("/dev/cu.usbmodem*")
    )
    return candidates[0] if candidates else None


def raw_repl_exec(ser, code: str, timeout: float = 30.0) -> tuple[str, str]:
    """
    Execute code on Pico via raw REPL.
    Returns (stdout, stderr).
    """
    # Send code in 256-byte chunks
    code_bytes = code.encode('utf-8')
    for i in range(0, len(code_bytes), 256):
        ser.write(code_bytes[i:i + 256])
        time.sleep(0.01)
    ser.write(b'\x04')  # Ctrl-D to execute

    # Read response until two \x04 and buffer ends with >
    response = b''
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            response += ser.read(ser.in_waiting)
            if response.count(b'\x04') >= 2 and response.endswith(b'>'):
                break
        else:
            time.sleep(0.05)

    # Parse: OK<stdout>\x04<stderr>\x04>
    text = response.decode('utf-8', errors='replace')
    if 'OK' in text:
        text = text.split('OK', 1)[1]
    parts = text.split('\x04')
    stdout = parts[0] if len(parts) > 0 else ""
    stderr = parts[1] if len(parts) > 1 else ""
    return stdout.strip(), stderr.strip()


def connect_pico(port: str):
    """Connect to Pico and enter raw REPL."""
    try:
        import serial
    except ImportError:
        print("Missing dependency: pyserial")
        print("  pip install pyserial")
        sys.exit(1)

    console = Console()
    console.print(f"[cyan]Connecting to {port}...[/]")

    ser = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.5)

    # Interrupt any running program
    ser.write(b'\r\x03\x03')
    time.sleep(0.5)
    ser.reset_input_buffer()

    # Enter raw REPL
    ser.write(b'\x01')
    time.sleep(0.5)

    # Drain banner
    while ser.in_waiting:
        ser.read(ser.in_waiting)
        time.sleep(0.1)

    console.print("[green]Connected to Pico raw REPL[/]")
    return ser


def list_bin_files(ser) -> list[tuple[str, int, str]]:
    """
    List flight logs on SD card.
    Returns list of (display_name, size, sd_path) tuples.
    Scans for per-flight folders (flight_001/flight.bin) first, then legacy flat .bin files.
    """
    code = """
import os
from machine import SPI, Pin
import sdcard
spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=1)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
for entry in os.listdir('/sd'):
    try:
        st = os.stat('/sd/' + entry)
        if st[0] & 0x4000:
            bp = '/sd/' + entry + '/flight.bin'
            try:
                bs = os.stat(bp)
                pf = 'y'
                try:
                    os.stat('/sd/' + entry + '/preflight.txt')
                except:
                    pf = 'n'
                print('F|' + entry + '|' + str(bs[6]) + '|' + entry + '/flight.bin|' + pf)
            except:
                pass
        elif entry.endswith('.bin'):
            print('L|' + entry + '|' + str(st[6]) + '|' + entry + '|n')
    except:
        pass
os.umount('/sd')
"""
    stdout, stderr = raw_repl_exec(ser, code)
    if stderr:
        raise RuntimeError(f"Error listing files: {stderr}")

    files = []
    for line in stdout.splitlines():
        line = line.strip()
        parts = line.split('|')
        if len(parts) >= 4:
            kind = parts[0]        # F=folder, L=legacy
            display = parts[1]     # folder name or filename
            size = int(parts[2])
            sd_path = parts[3]     # relative path on SD (e.g. "flight_001/flight.bin")
            has_preflight = len(parts) > 4 and parts[4] == 'y'
            label = display
            if has_preflight:
                label += ' [preflight]'
            files.append((label, size, sd_path))

    # Sort: folder-based flights by name descending, then legacy by name
    files.sort(key=lambda x: x[0], reverse=True)
    return files


def download_file(ser, sd_path: str, console: Console) -> bytes:
    """Download a file from Pico SD card via base64 chunks.
    sd_path is the path relative to /sd/ (e.g. 'flight_001/flight.bin' or 'flight.bin').
    """
    code = f"""
import os, ubinascii
from machine import SPI, Pin
import sdcard
spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=1)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
f = open('/sd/{sd_path}', 'rb')
while True:
    d = f.read(512)
    if not d:
        break
    print(ubinascii.b2a_base64(d).decode().strip())
print('EOF')
f.close()
os.umount('/sd')
"""
    # Send code
    code_bytes = code.encode('utf-8')
    for i in range(0, len(code_bytes), 256):
        ser.write(code_bytes[i:i + 256])
        time.sleep(0.01)
    ser.write(b'\x04')

    # Collect base64 lines until EOF
    data = bytearray()
    buffer = b''
    deadline = time.time() + 120  # 2 minute timeout for large files

    with Progress(
        TextColumn("[bold cyan]Downloading..."),
        BarColumn(),
        DownloadColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("download", total=None)

        while time.time() < deadline:
            if ser.in_waiting:
                buffer += ser.read(ser.in_waiting)
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    line = line.strip()
                    text = line.decode('utf-8', errors='replace')

                    if text == 'EOF':
                        # Read remaining REPL response
                        time.sleep(0.5)
                        while ser.in_waiting:
                            ser.read(ser.in_waiting)
                        progress.update(task, completed=len(data))
                        return bytes(data)

                    if text.startswith('OK'):
                        text = text[2:]

                    if text:
                        try:
                            chunk = base64.b64decode(text)
                            data.extend(chunk)
                            progress.update(task, completed=len(data))
                        except Exception:
                            pass  # Skip non-base64 lines (REPL noise)
            else:
                time.sleep(0.05)

    raise TimeoutError("Download timed out")


def run_download_mode(port: str | None = None) -> tuple[bytes, str]:
    """Interactive download from Pico. Returns (data, filename)."""
    console = Console()

    if port is None:
        port = find_pico_port()
        if port is None:
            console.print("[red]No Pico found. Connect via USB or specify --port[/]")
            sys.exit(1)

    ser = connect_pico(port)

    try:
        console.print("[cyan]Mounting SD card and listing files...[/]")
        files = list_bin_files(ser)

        if not files:
            console.print("[yellow]No flight logs found on SD card.[/]")
            ser.write(b'\x02')  # Exit raw REPL
            ser.close()
            sys.exit(1)

        console.print()
        console.print("[bold]Flight logs on SD card:[/]")
        for i, (display_name, size, _sd_path) in enumerate(files, 1):
            size_kb = size / 1024
            console.print(f"  [{i}] {display_name}  ({size_kb:.1f} KB)")

        console.print()
        choice = input("Select file number (or 'q' to quit): ").strip()
        if choice.lower() == 'q':
            ser.write(b'\x02')
            ser.close()
            sys.exit(0)

        idx = int(choice) - 1
        if idx < 0 or idx >= len(files):
            console.print("[red]Invalid selection[/]")
            ser.write(b'\x02')
            ser.close()
            sys.exit(1)

        display_name, _size, sd_path = files[idx]
        console.print(f"\n[cyan]Downloading {display_name}...[/]")
        data = download_file(ser, sd_path, console)
        console.print(f"[green]Downloaded {len(data)} bytes[/]")

        # Save locally — use display name (folder name or filename) for local file
        # Strip any annotation like " [preflight]" for the filename
        clean_name = display_name.split(' [')[0]
        local_path = Path(clean_name + '.bin' if not clean_name.endswith('.bin') else clean_name)
        local_path.write_bytes(data)
        console.print(f"[green]Saved to {local_path}[/]")
        filename = str(local_path)

    finally:
        # Clean up
        try:
            ser.write(b'\x02')  # Exit raw REPL
            ser.close()
        except Exception:
            pass

    return data, filename


# ── CSV Export ───────────────────────────────────────────────

def export_csv(flight: FlightData, output_path: str):
    """Export flight data to CSV."""
    if not flight.frames:
        return

    fields = list(flight.frames[0].keys())
    # Remove internal fields
    fields = [f for f in fields if f not in ("flags_list",)]
    fields.append("flags_str")

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for frame in flight.frames:
            row = dict(frame)
            row["flags_str"] = "|".join(frame["flags_list"]) if frame["flags_list"] else "SAFE"
            writer.writerow(row)


def save_summary(flight: FlightData, sim: SimData | None, output_path: str):
    """Save text summary to file."""
    lines = []
    lines.append("MPR ALTITUDE LOGGER — FLIGHT SUMMARY")
    lines.append("=" * 50)
    lines.append(f"Apogee:        {flight.max_alt:.1f} m AGL @ T+{flight.max_alt_time:.2f}s")
    lines.append(f"Max Velocity:  {flight.max_vel:.1f} m/s @ T+{flight.max_vel_time:.2f}s")
    lines.append(f"Max Accel:     ~{flight.max_accel:.1f} m/s^2 (est.)")
    lines.append(f"Flight Time:   {flight.duration:.1f} s")
    lines.append(f"Sample Rate:   {flight.sample_rate:.1f} Hz ({flight.n_frames} frames)")
    lines.append(f"Landing Vel:   {flight.landing_vel:.1f} m/s")
    lines.append("")
    lines.append(f"Errors:  {'YES' if flight.had_error else 'NONE'}")
    lines.append("")
    lines.append("State Transitions:")
    for tr in flight.transitions:
        lines.append(f"  T+{tr['time']:7.2f}s  {tr['from_state']} -> {tr['to_state']}")

    if sim:
        lines.append("")
        lines.append("SIMULATION COMPARISON")
        lines.append("-" * 50)
        lines.append(f"  Apogee:     actual {flight.max_alt:.1f}m  predicted {sim.max_alt:.1f}m  delta {flight.max_alt - sim.max_alt:+.1f}m")
        lines.append(f"  Max Vel:    actual {flight.max_vel:.1f}m/s  predicted {sim.max_vel:.1f}m/s  delta {flight.max_vel - sim.max_vel:+.1f}m/s")

    Path(output_path).write_text("\n".join(lines) + "\n")


# ── Interactive TUI ──────────────────────────────────────────

def get_key_nonblocking() -> str | None:
    """Non-blocking key read on macOS/Linux."""
    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1)
    return None


def run_tui(flight: FlightData, sim: SimData | None = None, source_file: str = ""):
    """Run the interactive TUI dashboard."""
    console = Console()

    # Save and set terminal to raw mode for keyboard input
    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())

        current_view = 0  # 0 = main dashboard
        running = True

        while running:
            console.clear()
            console.print()

            # Header
            title = f"[bold white on blue]  MPR ALTITUDE LOGGER — POST-FLIGHT ANALYSIS  [/]"
            if source_file:
                title += f"  [dim]{source_file}[/]"
            console.print(title)
            console.print()

            # Build and display dashboard
            renderables = build_full_dashboard(flight, sim)
            for r in renderables:
                console.print(r)

            # Controls bar
            console.print()
            controls = Text()
            controls.append("  [E]", style="bold cyan")
            controls.append(" Export CSV  ", style="dim")
            controls.append("[S]", style="bold cyan")
            controls.append(" Save Summary  ", style="dim")
            controls.append("[Q]", style="bold cyan")
            controls.append(" Quit", style="dim")
            console.print(Panel(controls, border_style="dim", padding=(0, 1)))

            # Wait for key
            key = None
            while key is None and running:
                key = get_key_nonblocking()
                if key is None:
                    time.sleep(0.05)

            if key is None:
                continue

            key = key.lower()

            if key == 'q':
                running = False

            elif key == 'e':
                csv_path = Path(source_file).with_suffix('.csv') if source_file else Path("flight_export.csv")
                export_csv(flight, str(csv_path))
                console.print(f"\n[green]Exported {flight.n_frames} frames to {csv_path}[/]")
                console.print("[dim]Press any key to continue...[/]")
                while get_key_nonblocking() is None:
                    time.sleep(0.05)

            elif key == 's':
                txt_path = Path(source_file).with_suffix('.txt') if source_file else Path("flight_summary.txt")
                save_summary(flight, sim, str(txt_path))
                console.print(f"\n[green]Saved summary to {txt_path}[/]")
                console.print("[dim]Press any key to continue...[/]")
                while get_key_nonblocking() is None:
                    time.sleep(0.05)

    finally:
        # Restore terminal
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


# ── Entry Point ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MPR Altitude Logger — Post-flight analysis TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              Download from Pico, then analyze
  %(prog)s flight.bin                   Analyze a local .bin file
  %(prog)s flight.bin --sim sim.csv     Compare actual vs simulated
  %(prog)s --download --port /dev/ttyACM0   Download from specific port
        """
    )
    parser.add_argument("logfile", nargs="?", help="Binary log file (.bin)")
    parser.add_argument("--sim", type=str, help="Simulation CSV for comparison")
    parser.add_argument("--port", type=str, help="Serial port for Pico connection")
    parser.add_argument("--download", action="store_true",
                        help="Force download mode (connect to Pico)")
    args = parser.parse_args()

    console = Console()

    # Determine mode
    data = None
    source_file = ""

    if args.logfile and not args.download:
        # Mode 2/3: direct file analysis
        source_file = args.logfile
        if not Path(source_file).exists():
            console.print(f"[red]File not found: {source_file}[/]")
            sys.exit(1)
        data = Path(source_file).read_bytes()
    else:
        # Mode 1: download from Pico
        data, source_file = run_download_mode(args.port)

    # Decode
    frames, version = decode_bin(data)
    if not frames:
        console.print("[red]No valid frames found in log data.[/]")
        sys.exit(1)

    console.print(f"[green]Decoded {len(frames)} frames (log version {version})[/]")

    flight = FlightData(frames, version)

    # Load sim data if provided
    sim = None
    if args.sim:
        if not Path(args.sim).exists():
            console.print(f"[yellow]Sim file not found: {args.sim}[/]")
        else:
            sim_rows = load_sim_csv(args.sim)
            sim = SimData(sim_rows)
            console.print(f"[green]Loaded {len(sim_rows)} simulation data points[/]")

    # Run TUI
    time.sleep(0.5)
    run_tui(flight, sim, source_file)


if __name__ == "__main__":
    main()
