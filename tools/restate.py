#!/usr/bin/env python3
"""
Post-flight state reprocessor TUI.

Interactive terminal dashboard that:
  1. Auto-detects mounted SD card (AVIONICS volume)
  2. Lists flight directories, showing which have been restated
  3. Replays altitude/velocity through the state machine offline
  4. Shows before/after comparison with animated graph rendering
  5. Saves restated .bin back to the flight directory on SD

Usage:
    python restate.py                   # auto-detect SD, interactive select
    python restate.py flight.bin        # reprocess a specific file
"""

import argparse
import glob
import os
import select
import struct
import sys
import termios
import time
import tty
from pathlib import Path

# The project's logging/ directory shadows Python's stdlib logging module,
# which rich needs. Remove cwd from sys.path before importing rich.
_cwd = os.getcwd()
_clean_path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(_cwd)]
_orig_path = sys.path[:]
sys.path = _clean_path

try:
    from rich.console import Console
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print("Missing dependency: rich")
    print("  pip install rich")
    sys.exit(1)
finally:
    sys.path = _orig_path


# ── Binary format constants ─────────────────────────────────────────────────

FRAME_HEADER = b'\xAA\x55'
FILE_HEADER_SIZE = 10

FRAME_FORMAT_V3 = '<IB f f f f f HHH B HH BB BB'
FRAME_SIZE_V3 = struct.calcsize(FRAME_FORMAT_V3)
FIELD_NAMES_V3 = [
    "timestamp_ms", "state", "pressure_pa", "temperature_c",
    "alt_raw_m", "alt_filtered_m", "vel_filtered_ms",
    "v_3v3_mv", "v_5v_mv", "v_9v_mv", "flags",
    "frame_us", "flush_us", "free_kb", "cpu_temp_c",
    "i2c_errors", "overruns"
]

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

STATE_NAMES = ["PAD", "BOOST", "COAST", "APOGEE", "DROGUE", "MAIN", "LANDED"]
STATE_COLORS = {
    "PAD": "white", "BOOST": "red", "COAST": "yellow",
    "APOGEE": "green", "DROGUE": "cyan", "MAIN": "blue",
    "LANDED": "magenta"
}

# Where the SD card mounts on macOS
SD_MOUNT_PATHS = ["/Volumes/AVIONICS", "/Volumes/ROCKET", "/Volumes/SD"]

# Default thresholds (mirrored from config.py)
DEFAULTS = {
    "launch_alt":       15.0,
    "launch_vel":       10.0,
    "launch_window":    0.5,
    "boost_recovery_alt": 10.0,
    "boost_recovery_window": 2.0,
    "boost_recovery_count": 3,
    "coast_vel_drop":   5.0,
    "coast_timeout":    30.0,
    "apogee_vel":       2.0,
    "apogee_count":     5,
    "apogee_dwell":     5,
    "main_fraction":    0.25,
    "landed_vel":       0.5,
    "landed_seconds":   5.0,
}

PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED = range(7)

SPARKLINE_CHARS = " ▁▂▃▄▅▆▇█"


# ── Offline State Machine ───────────────────────────────────────────────────

class OfflineStateMachine:
    """Replay state machine — pure Python, no hardware deps."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = PAD
        self.ground_alt = 0.0
        self.max_alt = 0.0
        self.max_vel = 0.0
        self.launch_time = 0
        self.apogee_time = 0
        self.coast_start = 0
        self._apogee_count = 0
        self._apogee_dwell = 0
        self._recovery_count = 0
        self._landed_start = 0
        self._launch_time_start = 0

    def set_ground_reference(self, alt):
        self.ground_alt = alt

    def update(self, alt, vel, now_ms):
        agl = alt - self.ground_alt

        if self.state >= BOOST:
            if alt > self.max_alt:
                self.max_alt = alt
            if vel > self.max_vel:
                self.max_vel = vel

        if self.state == PAD:
            if agl > self.cfg["launch_alt"] and vel > self.cfg["launch_vel"]:
                if self._launch_time_start == 0:
                    self._launch_time_start = now_ms
                elif (now_ms - self._launch_time_start) > self.cfg["launch_window"] * 1000:
                    self.state = BOOST
                    self.launch_time = now_ms
                    self.max_alt = alt
                    self.max_vel = vel
            else:
                self._launch_time_start = 0

        elif self.state == BOOST:
            if (now_ms - self.launch_time) < self.cfg["boost_recovery_window"] * 1000:
                if agl < self.cfg["boost_recovery_alt"]:
                    self._recovery_count += 1
                    if self._recovery_count >= self.cfg["boost_recovery_count"]:
                        self.state = PAD
                        self.launch_time = 0
                        self.max_alt = 0.0
                        self.max_vel = 0.0
                        self._launch_time_start = 0
                        self._recovery_count = 0
                else:
                    self._recovery_count = 0
            elif self.max_vel > 0 and vel < self.max_vel - self.cfg["coast_vel_drop"]:
                self.state = COAST
                self.coast_start = now_ms

        elif self.state == COAST:
            if vel < self.cfg["apogee_vel"]:
                self._apogee_count += 1
                if self._apogee_count >= self.cfg["apogee_count"]:
                    self.state = APOGEE
                    self.apogee_time = now_ms
            else:
                self._apogee_count = 0
            if self.coast_start and (now_ms - self.coast_start) > self.cfg["coast_timeout"] * 1000:
                self.state = APOGEE
                self.apogee_time = now_ms

        elif self.state == APOGEE:
            self._apogee_dwell += 1
            if self._apogee_dwell >= self.cfg["apogee_dwell"]:
                self.state = DROGUE

        elif self.state == DROGUE:
            max_agl = self.max_alt - self.ground_alt
            if max_agl > 0 and agl <= max_agl * self.cfg["main_fraction"] and vel < 0:
                self.state = MAIN
            self._check_landed(vel, now_ms)

        elif self.state == MAIN:
            self._check_landed(vel, now_ms)

        return self.state

    def _check_landed(self, vel, now_ms):
        if abs(vel) < self.cfg["landed_vel"]:
            if self._landed_start == 0:
                self._landed_start = now_ms
            elif (now_ms - self._landed_start) > self.cfg["landed_seconds"] * 1000:
                self.state = LANDED
        else:
            self._landed_start = 0


# ── Binary parsing ──────────────────────────────────────────────────────────

def detect_format(data):
    """Detect log version and return (version, fmt, frame_size, fields, data_offset)."""
    if data[:6] == b'RKTLOG':
        version, fsize = struct.unpack_from('<HH', data, 6)
        offset = FILE_HEADER_SIZE
    else:
        version = 2
        offset = 0

    if version >= 3:
        return version, FRAME_FORMAT_V3, FRAME_SIZE_V3, FIELD_NAMES_V3, offset
    elif version >= 2:
        return version, FRAME_FORMAT_V2, FRAME_SIZE_V2, FIELD_NAMES_V2, offset
    else:
        return version, FRAME_FORMAT_V1, FRAME_SIZE_V1, FIELD_NAMES_V1, offset


def parse_frames(data):
    """Parse binary data into frames + byte offsets for patching."""
    version, fmt, fsize, fields, offset = detect_format(data)

    frames = []
    frame_offsets = []
    skipped = 0

    while offset + 2 + fsize <= len(data):
        if data[offset:offset + 2] != FRAME_HEADER:
            offset += 1
            skipped += 1
            continue

        frame_data_offset = offset + 2
        offset += 2

        if offset + fsize > len(data):
            break

        values = struct.unpack_from(fmt, data, offset)
        offset += fsize

        frame = dict(zip(fields, values))
        frames.append(frame)
        frame_offsets.append(frame_data_offset)

    return frames, frame_offsets, version, fsize, skipped


def reprocess(frames, cfg):
    """Run all frames through the offline state machine."""
    if not frames:
        return [], []

    sm = OfflineStateMachine(cfg)

    pad_frames = min(25, len(frames))
    ground_alt = sum(f["alt_filtered_m"] for f in frames[:pad_frames]) / pad_frames
    sm.set_ground_reference(ground_alt)

    transitions = []
    prev_state = PAD

    for f in frames:
        ts = f["timestamp_ms"]
        alt = f["alt_filtered_m"]
        vel = f["vel_filtered_ms"]

        new_state = sm.update(alt, vel, ts)
        f["restate"] = new_state

        if new_state != prev_state:
            t_sec = (ts - frames[0]["timestamp_ms"]) / 1000
            transitions.append({
                "time": t_sec,
                "state": STATE_NAMES[new_state],
                "agl": alt - ground_alt,
                "vel": vel,
            })
            prev_state = new_state

    return frames, transitions


def write_restated_bin(input_data, frames, frame_offsets, output_path):
    """Write a new .bin with state bytes patched. Original data untouched."""
    out = bytearray(input_data)

    patched = 0
    for frame, data_offset in zip(frames, frame_offsets):
        state_byte_offset = data_offset + 4  # u32 timestamp = 4 bytes, then u8 state
        old_state = out[state_byte_offset]
        new_state = frame["restate"]
        if old_state != new_state:
            out[state_byte_offset] = new_state
            patched += 1

    Path(output_path).write_bytes(bytes(out))
    return patched


# ── SD Card Detection ───────────────────────────────────────────────────────

def find_sd_mount():
    """Find mounted AVIONICS SD card. Returns path or None."""
    for path in SD_MOUNT_PATHS:
        if os.path.isdir(path):
            return path
    # Also check any /Volumes/ that contain flight_* dirs
    try:
        for vol in os.listdir("/Volumes"):
            vpath = f"/Volumes/{vol}"
            if os.path.isdir(vpath):
                entries = os.listdir(vpath)
                if any(e.startswith("flight_") for e in entries):
                    return vpath
    except OSError:
        pass
    return None


def list_flights(sd_path):
    """
    List flight directories on SD card.
    Returns list of dicts: {name, path, size_kb, has_preflight, has_restated, bin_path}
    """
    flights = []
    try:
        entries = sorted(os.listdir(sd_path))
    except OSError:
        return flights

    for entry in entries:
        flight_dir = os.path.join(sd_path, entry)
        if not os.path.isdir(flight_dir):
            continue

        bin_path = os.path.join(flight_dir, "flight.bin")
        if not os.path.isfile(bin_path):
            continue

        size = os.path.getsize(bin_path)
        has_preflight = os.path.isfile(os.path.join(flight_dir, "preflight.txt"))
        has_restated = os.path.isfile(os.path.join(flight_dir, "flight_restated.bin"))

        flights.append({
            "name": entry,
            "path": flight_dir,
            "bin_path": bin_path,
            "size_kb": size / 1024,
            "has_preflight": has_preflight,
            "has_restated": has_restated,
        })

    return flights


# ── TUI Rendering ───────────────────────────────────────────────────────────

def sparkline(values, width=50):
    """Generate a sparkline string from values."""
    if not values:
        return ""
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


def render_altitude_chart(times, altitudes, width=60, height=18,
                          transitions=None, label="", color="blue"):
    """Render altitude vs time as ASCII column chart."""
    if not times or not altitudes:
        return "(no data)"

    max_alt = max(altitudes) * 1.1
    max_alt = max(max_alt, 1.0)
    max_t = max(times)
    if max_t <= 0:
        return "(no time data)"

    label_w = 7
    lines = []

    for row in range(height, -1, -1):
        threshold = (row / height) * max_alt
        if row % 4 == 0 or row == height:
            lbl = f"{threshold:5.0f}m"
        else:
            lbl = " " * 6
        lbl = lbl.rjust(label_w - 1) + "|"

        chars = []
        for col in range(width):
            t = (col / width) * max_t
            idx = min(range(len(times)), key=lambda i, t=t: abs(times[i] - t))
            alt = altitudes[idx]

            if alt >= threshold:
                chars.append(f"[bold {color}]\u2588[/bold {color}]")
            else:
                chars.append(" ")

        lines.append(lbl + "".join(chars))

    # X-axis
    lines.append(" " * label_w + "\u2500" * width)
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
            sc = STATE_COLORS.get(tr["state"], "white")
            event_line += f"[{sc}]\u25bc{tr['state']}[/]"
        lines.append(event_line)

    return "\n".join(lines)


def render_state_timeline(states, times, transitions, width=50):
    """Render state timeline as colored bar."""
    if not states or not times:
        return Text("(no data)")

    text = Text()
    max_t = times[-1]
    if max_t <= 0:
        return Text("(no time data)")

    # Build segments
    segments = []
    current_state = STATE_NAMES[states[0]] if states[0] < len(STATE_NAMES) else "?"
    start_t = 0.0

    for tr in transitions:
        segments.append((start_t, tr["time"], current_state))
        current_state = tr["state"]
        start_t = tr["time"]
    segments.append((start_t, max_t, current_state))

    for start, end, state in segments:
        frac = (end - start) / max_t
        n_chars = max(1, int(frac * width))
        color = STATE_COLORS.get(state, "white")
        text.append(state, style=f"bold {color}")
        text.append(" ")
        text.append("\u2588" * n_chars, style=color)
        text.append(" ")

    return text


def get_key_nonblocking():
    """Non-blocking key read."""
    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1)
    return None


def wait_key():
    """Block until a key is pressed."""
    while True:
        k = get_key_nonblocking()
        if k is not None:
            return k
        time.sleep(0.05)


# ── TUI Screens ────────────────────────────────────────────────────────────

def render_flight_select(console, flights, selected, sd_path):
    """Render the flight selection screen."""
    console.clear()
    console.print()
    console.print("[bold white on blue]  MPR ALTITUDE LOGGER — STATE REPROCESSOR  [/]")
    console.print()
    console.print(f"  [dim]SD card:[/] [cyan]{sd_path}[/]")
    console.print()

    table = Table(box=box.ROUNDED, border_style="dim", padding=(0, 1))
    table.add_column("", width=3, justify="center")
    table.add_column("Flight", width=20)
    table.add_column("Size", width=10, justify="right")
    table.add_column("Status", width=30)

    for i, f in enumerate(flights):
        pointer = "[bold cyan]\u25b6[/]" if i == selected else " "
        name = f"[bold]{f['name']}[/]" if i == selected else f["name"]

        size = f"{f['size_kb']:.1f} KB"

        tags = []
        if f["has_preflight"]:
            tags.append("[green]preflight[/]")
        if f["has_restated"]:
            tags.append("[yellow]restated[/]")
        status = "  ".join(tags) if tags else "[dim]—[/]"

        row_style = "bold" if i == selected else ""
        table.add_row(pointer, name, size, status, style=row_style)

    console.print(table)
    console.print()

    controls = Text()
    controls.append("  \u2191\u2193", style="bold cyan")
    controls.append(" Select  ", style="dim")
    controls.append("Enter", style="bold cyan")
    controls.append(" Reprocess  ", style="dim")
    controls.append("Q", style="bold cyan")
    controls.append(" Quit", style="dim")
    console.print(Panel(controls, border_style="dim", padding=(0, 1)))


def render_comparison_screen(console, flight_name, frames, orig_transitions,
                             new_transitions, cfg, animate_pct=1.0):
    """Render the before/after comparison screen."""
    console.clear()
    console.print()
    console.print(f"[bold white on blue]  RESTATE — {flight_name}  [/]")
    console.print()

    t0 = frames[0]["timestamp_ms"]
    duration = (frames[-1]["timestamp_ms"] - t0) / 1000

    # Ground reference
    pad_n = min(25, len(frames))
    ground = sum(f["alt_filtered_m"] for f in frames[:pad_n]) / pad_n

    # Compute data arrays
    times = [(f["timestamp_ms"] - t0) / 1000 for f in frames]
    altitudes = [f["alt_filtered_m"] - ground for f in frames]
    velocities = [f["vel_filtered_ms"] for f in frames]
    orig_states = [f["state"] for f in frames]
    new_states = [f["restate"] for f in frames]

    max_alt = max(altitudes)
    max_vel = max(velocities)

    # Animated subset
    n_show = max(1, int(len(frames) * animate_pct))

    # ── Stats panel ────
    n_diff = sum(1 for f in frames[:n_show] if f["state"] != f["restate"])
    pct_diff = n_diff / n_show * 100 if n_show > 0 else 0

    stats_lines = []
    stats_lines.append(f"  [bold]Duration[/]      [cyan]{duration:.1f} s[/]    ({len(frames):,} frames)")
    stats_lines.append(f"  [bold]Max Altitude[/]  [cyan]{max_alt:.1f} m AGL[/]")
    stats_lines.append(f"  [bold]Max Velocity[/]  [cyan]{max_vel:.1f} m/s[/]")
    stats_lines.append(f"  [bold]Frames Changed[/] [{'yellow' if n_diff > 0 else 'green'}]{n_diff}[/] ({pct_diff:.1f}%)")
    console.print(Panel("\n".join(stats_lines), title="[bold white]FLIGHT STATS[/]",
                        border_style="cyan", padding=(0, 2)))

    # ── Side-by-side altitude charts ────
    chart_w = 35
    chart_h = 14

    orig_chart = render_altitude_chart(
        times[:n_show], altitudes[:n_show],
        width=chart_w, height=chart_h,
        transitions=orig_transitions, color="blue",
    )
    new_chart = render_altitude_chart(
        times[:n_show], altitudes[:n_show],
        width=chart_w, height=chart_h,
        transitions=new_transitions, color="green",
    )

    orig_panel = Panel(
        Text.from_markup(orig_chart + f"\n\n  [bold blue]\u2588[/] Original states"),
        title="[bold white]BEFORE (onboard)[/]",
        border_style="blue",
        padding=(0, 1),
        width=chart_w + 14,
    )
    new_panel = Panel(
        Text.from_markup(new_chart + f"\n\n  [bold green]\u2588[/] Reprocessed states"),
        title="[bold white]AFTER (restated)[/]",
        border_style="green",
        padding=(0, 1),
        width=chart_w + 14,
    )

    # Print side by side
    console.print(Columns([orig_panel, new_panel], equal=True, expand=True))

    # ── State timelines ────
    console.print()
    orig_tl = render_state_timeline(orig_states[:n_show], times[:n_show], orig_transitions, width=40)
    new_tl = render_state_timeline(new_states[:n_show], times[:n_show], new_transitions, width=40)

    tl_table = Table(box=None, show_header=True, padding=(0, 2), expand=True)
    tl_table.add_column("Original", justify="left")
    tl_table.add_column("Restated", justify="left")
    tl_table.add_row(orig_tl, new_tl)
    console.print(tl_table)

    # ── Velocity sparkline ────
    vel_spark = sparkline(velocities[:n_show], width=60)
    console.print(Panel(
        f"  Vel  {vel_spark}  [cyan]{max_vel:+.1f} m/s peak[/]",
        title="[bold white]VELOCITY[/]", border_style="dim", padding=(0, 1),
    ))

    # ── Transition comparison table ────
    trans_table = Table(box=box.SIMPLE, padding=(0, 2), expand=True)
    trans_table.add_column("", width=10, style="bold")
    trans_table.add_column("Original (onboard)", width=30)
    trans_table.add_column("Restated (offline)", width=30)

    # Collect all unique states that appear in either
    all_states_seen = set()
    for tr in orig_transitions:
        all_states_seen.add(tr["state"])
    for tr in new_transitions:
        all_states_seen.add(tr["state"])

    state_order = [s for s in STATE_NAMES if s in all_states_seen]

    for state in state_order:
        sc = STATE_COLORS.get(state, "white")
        orig_t = next((tr for tr in orig_transitions if tr["state"] == state), None)
        new_t = next((tr for tr in new_transitions if tr["state"] == state), None)

        orig_str = f"T+{orig_t['time']:.2f}s" if orig_t else "[dim]—[/]"
        new_str = f"T+{new_t['time']:.2f}s" if new_t else "[dim]—[/]"

        # Highlight differences
        if orig_t and new_t:
            delta = new_t["time"] - orig_t["time"]
            if abs(delta) > 0.1:
                new_str += f"  [yellow]({delta:+.2f}s)[/]"
        elif orig_t and not new_t:
            new_str = "[red]missing[/]"
        elif new_t and not orig_t:
            new_str = f"[green]T+{new_t['time']:.2f}s (new)[/]"
            orig_str = "[dim]missing[/]"

        trans_table.add_row(f"[{sc}]{state}[/]", orig_str, new_str)

    if state_order:
        console.print(Panel(trans_table, title="[bold white]STATE TRANSITIONS[/]",
                            border_style="dim", padding=(0, 1)))

    # ── Active thresholds (compact) ────
    overridden = {k: v for k, v in cfg.items() if v != DEFAULTS[k]}
    if overridden:
        thresh_parts = [f"{k}={v}" for k, v in overridden.items()]
        console.print(f"  [dim]Overrides: {', '.join(thresh_parts)}[/]")


def render_save_status(console, message, style="green"):
    """Show save status message."""
    console.print()
    console.print(f"  [{style}]{message}[/]")
    console.print()


# ── Main TUI Loop ──────────────────────────────────────────────────────────

def run_select_screen(console, sd_path):
    """Run flight selection screen. Returns selected flight dict or None."""
    flights = list_flights(sd_path)

    if not flights:
        console.print("[yellow]No flight directories found on SD card.[/]")
        console.print(f"[dim]Looked in: {sd_path}[/]")
        return None

    selected = 0

    while True:
        render_flight_select(console, flights, selected, sd_path)

        key = wait_key()

        if key == 'q' or key == 'Q':
            return None

        # Arrow keys come as escape sequences: ESC [ A/B
        if key == '\x1b':
            # Read the rest of the escape sequence
            time.sleep(0.02)
            seq = ""
            while True:
                k = get_key_nonblocking()
                if k is None:
                    break
                seq += k
            if seq == '[A':  # Up
                selected = max(0, selected - 1)
            elif seq == '[B':  # Down
                selected = min(len(flights) - 1, selected + 1)

        elif key in ('k', 'K'):
            selected = max(0, selected - 1)
        elif key in ('j', 'J'):
            selected = min(len(flights) - 1, selected + 1)

        elif key in ('\r', '\n'):
            return flights[selected]


def run_comparison_screen(console, flight_info, cfg):
    """Run the before/after comparison screen for a flight."""
    bin_path = flight_info["bin_path"]
    flight_name = flight_info["name"]

    # Load and parse
    raw_data = Path(bin_path).read_bytes()
    frames, frame_offsets, version, fsize, skipped = parse_frames(raw_data)

    if not frames:
        console.clear()
        console.print(f"[red]No valid frames in {bin_path}[/]")
        console.print("[dim]Press any key...[/]")
        wait_key()
        return

    # Compute original transitions
    t0 = frames[0]["timestamp_ms"]
    orig_transitions = []
    for i in range(1, len(frames)):
        if frames[i]["state"] != frames[i - 1]["state"]:
            t = (frames[i]["timestamp_ms"] - t0) / 1000
            s = frames[i]["state"]
            orig_transitions.append({
                "time": t,
                "state": STATE_NAMES[s] if s < len(STATE_NAMES) else "?",
                "agl": 0,
                "vel": 0,
            })

    # Reprocess
    frames, new_transitions = reprocess(frames, cfg)

    # Animate the chart drawing
    animate_steps = [0.02, 0.05, 0.1, 0.15, 0.25, 0.4, 0.6, 0.8, 1.0]

    for pct in animate_steps:
        render_comparison_screen(console, flight_name, frames, orig_transitions,
                                 new_transitions, cfg, animate_pct=pct)
        time.sleep(0.08)

    # Final render with controls
    saved = False
    while True:
        render_comparison_screen(console, flight_name, frames, orig_transitions,
                                 new_transitions, cfg, animate_pct=1.0)

        # Controls
        console.print()
        controls = Text()
        controls.append("  [R]", style="bold cyan")
        controls.append(" Save restated .bin  ", style="dim")
        controls.append("[T]", style="bold cyan")
        controls.append(" Edit thresholds  ", style="dim")
        controls.append("[B]", style="bold cyan")
        controls.append(" Back  ", style="dim")
        controls.append("[Q]", style="bold cyan")
        controls.append(" Quit", style="dim")
        console.print(Panel(controls, border_style="dim", padding=(0, 1)))

        if saved:
            console.print(f"  [bold green]\u2713 Saved flight_restated.bin to {flight_info['path']}[/]")

        key = wait_key()

        if key in ('q', 'Q'):
            return "quit"

        elif key in ('b', 'B'):
            return "back"

        elif key in ('r', 'R'):
            # Save restated bin to the flight directory
            out_path = os.path.join(flight_info["path"], "flight_restated.bin")
            patched = write_restated_bin(raw_data, frames, frame_offsets, out_path)
            saved = True
            flight_info["has_restated"] = True

        elif key in ('t', 'T'):
            # Threshold edit screen
            new_cfg = run_threshold_screen(console, cfg)
            if new_cfg is not None:
                cfg.update(new_cfg)
                # Re-run reprocess with new thresholds
                # Reset restates
                for f in frames:
                    if "restate" in f:
                        del f["restate"]
                frames, new_transitions = reprocess(frames, cfg)
                saved = False

                # Re-animate
                for pct in animate_steps:
                    render_comparison_screen(console, flight_name, frames, orig_transitions,
                                             new_transitions, cfg, animate_pct=pct)
                    time.sleep(0.08)


def run_threshold_screen(console, cfg):
    """Interactive threshold editor. Returns updated cfg dict or None."""
    keys = list(cfg.keys())
    selected = 0

    # Work on a copy
    editing = dict(cfg)

    while True:
        console.clear()
        console.print()
        console.print("[bold white on blue]  THRESHOLD EDITOR  [/]")
        console.print()
        console.print("  [dim]Adjust thresholds to tune state detection.[/]")
        console.print("  [dim]Changes marked with * differ from firmware defaults.[/]")
        console.print()

        table = Table(box=box.ROUNDED, border_style="dim", padding=(0, 1))
        table.add_column("", width=3, justify="center")
        table.add_column("Parameter", width=30)
        table.add_column("Value", width=12, justify="right")
        table.add_column("Default", width=12, justify="right", style="dim")
        table.add_column("", width=3)

        for i, k in enumerate(keys):
            pointer = "[bold cyan]\u25b6[/]" if i == selected else " "
            val = editing[k]
            default = DEFAULTS[k]
            changed = " *" if val != default else ""

            if isinstance(val, float):
                val_str = f"{val:.2f}"
                def_str = f"{default:.2f}"
            else:
                val_str = str(val)
                def_str = str(default)

            style = "bold yellow" if val != default else ""
            table.add_row(pointer, k, val_str, def_str, changed, style=style)

        console.print(table)
        console.print()

        controls = Text()
        controls.append("  \u2191\u2193", style="bold cyan")
        controls.append(" Select  ", style="dim")
        controls.append("\u2190\u2192", style="bold cyan")
        controls.append(" Adjust  ", style="dim")
        controls.append("D", style="bold cyan")
        controls.append(" Reset to default  ", style="dim")
        controls.append("Enter", style="bold cyan")
        controls.append(" Apply  ", style="dim")
        controls.append("Esc", style="bold cyan")
        controls.append(" Cancel", style="dim")
        console.print(Panel(controls, border_style="dim", padding=(0, 1)))

        key = wait_key()

        if key == '\x1b':
            time.sleep(0.02)
            seq = ""
            while True:
                k = get_key_nonblocking()
                if k is None:
                    break
                seq += k

            if seq == '[A':  # Up
                selected = max(0, selected - 1)
            elif seq == '[B':  # Down
                selected = min(len(keys) - 1, selected + 1)
            elif seq == '[C':  # Right — increase
                k = keys[selected]
                editing[k] = _adjust_value(editing[k], k, +1)
            elif seq == '[D':  # Left — decrease
                k = keys[selected]
                editing[k] = _adjust_value(editing[k], k, -1)
            elif seq == '':
                # Plain Esc — cancel
                return None

        elif key in ('k', 'K'):
            selected = max(0, selected - 1)
        elif key in ('j', 'J'):
            selected = min(len(keys) - 1, selected + 1)

        elif key in ('d', 'D'):
            k = keys[selected]
            editing[k] = DEFAULTS[k]

        elif key in ('\r', '\n'):
            return editing


def _adjust_value(val, key, direction):
    """Adjust a threshold value up or down."""
    if isinstance(val, int):
        return max(1, val + direction)

    # Float — step size depends on the parameter
    steps = {
        "launch_alt": 1.0,
        "launch_vel": 1.0,
        "launch_window": 0.1,
        "boost_recovery_alt": 1.0,
        "boost_recovery_window": 0.5,
        "coast_vel_drop": 0.5,
        "coast_timeout": 1.0,
        "apogee_vel": 0.5,
        "main_fraction": 0.05,
        "landed_vel": 0.1,
        "landed_seconds": 0.5,
    }
    step = steps.get(key, 0.5)
    new_val = val + (step * direction)
    return max(0.01, round(new_val, 2))


def run_tui(sd_path=None, bin_file=None, cfg=None):
    """Main TUI entry point."""
    console = Console()

    if cfg is None:
        cfg = dict(DEFAULTS)

    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())

        if bin_file:
            # Direct file mode — build a fake flight_info
            p = Path(bin_file)
            flight_info = {
                "name": p.stem,
                "path": str(p.parent),
                "bin_path": str(p),
                "size_kb": p.stat().st_size / 1024,
                "has_preflight": False,
                "has_restated": False,
            }
            result = run_comparison_screen(console, flight_info, cfg)
            return

        if sd_path is None:
            sd_path = find_sd_mount()

        if sd_path is None:
            console.clear()
            console.print()
            console.print("[bold white on blue]  MPR ALTITUDE LOGGER — STATE REPROCESSOR  [/]")
            console.print()
            console.print("[red]No SD card detected.[/]")
            console.print()
            console.print("[dim]Mount the AVIONICS SD card and try again, or specify a file:[/]")
            console.print("[dim]  python restate.py flight.bin[/]")
            console.print()
            return

        while True:
            flight = run_select_screen(console, sd_path)
            if flight is None:
                break

            result = run_comparison_screen(console, flight, cfg)
            if result == "quit":
                break
            # "back" → loop back to select screen

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        console.clear()


# ── Entry Point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MPR Altitude Logger — State Reprocessor TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python restate.py                            # auto-detect SD card
  python restate.py flight.bin                 # reprocess a specific file
  python restate.py --launch-alt 10            # override threshold
        """,
    )
    parser.add_argument("logfile", nargs="?", help="Binary flight log (.bin)")
    parser.add_argument("--sd", type=str, help="SD card mount path (auto-detected)")

    g = parser.add_argument_group("threshold overrides (defaults from config.py)")
    g.add_argument("--launch-alt", type=float)
    g.add_argument("--launch-vel", type=float)
    g.add_argument("--launch-window", type=float)
    g.add_argument("--coast-vel-drop", type=float)
    g.add_argument("--coast-timeout", type=float)
    g.add_argument("--apogee-vel", type=float)
    g.add_argument("--apogee-count", type=int)
    g.add_argument("--main-fraction", type=float)
    g.add_argument("--landed-vel", type=float)
    g.add_argument("--landed-seconds", type=float)

    args = parser.parse_args()

    # Build config
    cfg = dict(DEFAULTS)
    override_map = {
        "launch_alt": args.launch_alt,
        "launch_vel": args.launch_vel,
        "launch_window": args.launch_window,
        "coast_vel_drop": args.coast_vel_drop,
        "coast_timeout": args.coast_timeout,
        "apogee_vel": args.apogee_vel,
        "apogee_count": args.apogee_count,
        "main_fraction": args.main_fraction,
        "landed_vel": args.landed_vel,
        "landed_seconds": args.landed_seconds,
    }
    for k, v in override_map.items():
        if v is not None:
            cfg[k] = v

    if args.logfile:
        p = Path(args.logfile)
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)
        run_tui(bin_file=str(p), cfg=cfg)
    else:
        run_tui(sd_path=args.sd, cfg=cfg)


if __name__ == "__main__":
    main()
