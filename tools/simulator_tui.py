#!/usr/bin/env python3
"""
Flight Simulator TUI — run synthetic flights through the full avionics pipeline.

Interactive terminal dashboard for testing the sensor→filter→FSM→logger pipeline
with various scenarios: normal flights, sensor faults, edge cases, and more.

Usage:
    python tools/simulator_tui.py
    pnpm simulator

Dependencies: rich
"""

import sys
import os
import time
import types
from unittest.mock import MagicMock

# ── MicroPython mocks (must run before importing avionics code) ──

import time as _time
if not hasattr(_time, 'ticks_ms'):
    _time.ticks_ms = lambda: int(_time.time() * 1000)
if not hasattr(_time, 'ticks_us'):
    _time.ticks_us = lambda: int(_time.time() * 1000000)
if not hasattr(_time, 'ticks_diff'):
    _time.ticks_diff = lambda a, b: a - b
if not hasattr(_time, 'sleep_ms'):
    _time.sleep_ms = lambda ms: _time.sleep(ms / 1000.0)

for mod_name in ('machine', '_thread', 'sdcard'):
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        for attr in ('Pin', 'SPI', 'SoftI2C', 'ADC', 'freq', 'WDT',
                      'start_new_thread', 'SDCard'):
            setattr(m, attr, MagicMock())
        sys.modules[mod_name] = m

# ── Path setup ──

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tests_dir = os.path.join(_project_root, 'tests')
for p in (_project_root, _tests_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Now safe to import ──

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print("Missing dependency: rich")
    print("  pip install rich")
    sys.exit(1)

from sim_harness import (
    PicoSim, SimResult,
    from_simulate, from_pressure_sequence, constant, noise_overlay,
    sensor_dropout, pressure_spike, gradual_drift, stuck_sensor,
    intermittent_dropout, angled_flight, below_ground_landing,
    temperature_ramp, SENSOR_FAULT,
)
from flight.state_machine import PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED, STATE_NAMES

console = Console()

# ── Sparkline ────────────────────────────────────────────────

SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def sparkline(values, width=60):
    """Unicode sparkline from a list of floats."""
    if not values:
        return " " * width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = list(values)
    lo, hi = min(sampled), max(sampled)
    span = hi - lo if hi > lo else 1.0
    n = len(SPARK_CHARS) - 1
    line = ""
    for v in sampled:
        idx = int((v - lo) / span * n)
        idx = max(0, min(n, idx))
        line += SPARK_CHARS[idx]
    return line


# ── State colors ─────────────────────────────────────────────

STATE_COLORS = {
    PAD: "white", BOOST: "red", COAST: "yellow", APOGEE: "green",
    DROGUE: "cyan", MAIN: "blue", LANDED: "magenta",
}


def state_badge(state):
    """Colored state name."""
    color = STATE_COLORS.get(state, "white")
    name = STATE_NAMES[state] if state < len(STATE_NAMES) else "?"
    return f"[bold {color}]{name}[/bold {color}]"


# ── Scenarios ────────────────────────────────────────────────

SCENARIOS = [
    {
        "key": "1",
        "name": "Normal Flight (H100)",
        "desc": "Cesaroni H100, 2.5kg, nominal — full PAD→LANDED sequence",
        "run": lambda: PicoSim(from_simulate(motor='Cesaroni_H100')).run(),
    },
    {
        "key": "2",
        "name": "Ideal Flight (I218)",
        "desc": "Perfect conditions — I218, 3kg, no noise, no faults",
        "run": lambda: PicoSim(from_simulate(
            motor='Cesaroni_I218', mass=3.0, cd=0.42, diameter=0.054,
        )).run(),
    },
    {
        "key": "3",
        "name": "High Altitude (J350)",
        "desc": "Aerotech J350, 5kg, 75mm — high apogee, numerical stress test",
        "run": lambda: PicoSim(from_simulate(
            motor='Aerotech_J350', mass=5.0, cd=0.45, diameter=0.075,
        )).run(),
    },
    {
        "key": "4",
        "name": "Short Flight (D12)",
        "desc": "Estes D12, 0.3kg, 25mm — minimal flight, low apogee",
        "run": lambda: PicoSim(from_simulate(
            motor='Estes_D12', mass=0.3, cd=0.5, diameter=0.025,
        )).run(),
    },
    {
        "key": "5",
        "name": "Noisy Sensors (100 Pa)",
        "desc": "H100 with 100 Pa barometer noise — tests Kalman robustness",
        "run": lambda: PicoSim(noise_overlay(
            from_simulate(motor='Cesaroni_H100'), noise_std=100.0,
        )).run(),
    },
    {
        "key": "6",
        "name": "Angled Flight (30% loss)",
        "desc": "Rocket tips off-axis — only reaches ~70% of nominal altitude",
        "run": lambda: PicoSim(angled_flight(
            from_simulate(motor='Cesaroni_H100'), effective_fraction=0.7,
        )).run(),
    },
    {
        "key": "7",
        "name": "Angled Flight (severe, 50% loss)",
        "desc": "Rocket tips badly — reaches ~50% altitude, tests low-apogee detection",
        "run": lambda: PicoSim(angled_flight(
            from_simulate(motor='Cesaroni_H100'), effective_fraction=0.5,
        )).run(),
    },
    {
        "key": "8",
        "name": "Sensor Dropout (mid-flight)",
        "desc": "10 frames of sensor fault during coast — tests error recovery",
        "run": lambda: _run_dropout(),
    },
    {
        "key": "9",
        "name": "Multiple Sensor Dropouts",
        "desc": "3 dropout windows throughout flight — barometer I2C issues",
        "run": lambda: _run_multi_dropout(),
    },
    {
        "key": "a",
        "name": "Pressure Spike at Apogee",
        "desc": "Single-frame pressure glitch near apogee — tests filter rejection",
        "run": lambda: _run_spike(),
    },
    {
        "key": "b",
        "name": "Stuck Sensor (100 frames)",
        "desc": "Barometer freezes for 4s during coast — tests graceful handling",
        "run": lambda: _run_stuck(),
    },
    {
        "key": "c",
        "name": "Below-Ground Landing",
        "desc": "Rocket lands in a valley 50m below launch site — negative AGL",
        "run": lambda: PicoSim(below_ground_landing(
            from_simulate(motor='Cesaroni_H100'), valley_depth_pa=600.0,
        )).run(),
    },
    {
        "key": "d",
        "name": "Wind Gust on Pad",
        "desc": "Slow barometric drift on pad — should NOT false-trigger launch",
        "run": lambda: PicoSim(gradual_drift(
            constant(pressure_pa=101325.0, n_frames=500),
            start=100, rate_pa_per_frame=0.8,
        )).run(),
    },
    {
        "key": "e",
        "name": "Temperature Shock",
        "desc": "Temperature ramps +0.05°C/frame during flight — thermal stress",
        "run": lambda: PicoSim(temperature_ramp(
            from_simulate(motor='Cesaroni_H100'), rate_c_per_frame=0.05,
        )).run(),
    },
    {
        "key": "f",
        "name": "Extended Pad Wait",
        "desc": "10 minutes on pad before H100 launch — long idle then flight",
        "run": lambda: _run_extended_pad(),
    },
    {
        "key": "g",
        "name": "Voltage Brownout",
        "desc": "3.3V drops to 2.8V, 5V to 4.2V — low battery during flight",
        "run": lambda: _run_brownout(),
    },
    {
        "key": "h",
        "name": "Heavy Pad Noise (200 Pa)",
        "desc": "Extreme noise on pad — must NOT false-trigger launch detect",
        "run": lambda: PicoSim(noise_overlay(
            constant(pressure_pa=101325.0, n_frames=500), noise_std=200.0,
        )).run(),
    },
    {
        "key": "i",
        "name": "Binary Round-Trip",
        "desc": "Write .bin file → decode → verify data integrity",
        "run": lambda: PicoSim(from_simulate(motor='Cesaroni_H100'), write_bin=True).run(),
    },
]


def _run_dropout():
    base = list(from_simulate(motor='Cesaroni_H100'))
    at = len(base) // 3
    return PicoSim(sensor_dropout(iter(base), at=at, duration=10)).run()


def _run_multi_dropout():
    base = list(from_simulate(motor='Cesaroni_H100'))
    n = len(base)
    intervals = [(n // 6, 8), (n // 3, 12), (n // 2, 5)]
    return PicoSim(intermittent_dropout(iter(base), intervals)).run()


def _run_spike():
    base = list(from_simulate(motor='Cesaroni_H100'))
    at = len(base) // 2
    return PicoSim(pressure_spike(iter(base), at=at, value=50000.0, duration=1)).run()


def _run_stuck():
    base = list(from_simulate(motor='Cesaroni_H100'))
    at = len(base) // 3
    return PicoSim(stuck_sensor(iter(base), at=at, duration=100)).run()


def _run_extended_pad():
    pad_frames = list(constant(pressure_pa=101325.0, n_frames=15000))
    flight_frames = list(from_simulate(motor='Cesaroni_H100'))
    return PicoSim(iter(pad_frames + flight_frames)).run()


def _run_brownout():
    def low_voltage():
        while True:
            yield (2800, 4200, 7500)
    return PicoSim(
        from_simulate(motor='Cesaroni_H100'),
        voltage_provider=low_voltage(),
    ).run()


# ── Display helpers ──────────────────────────────────────────

def render_result(name, result):
    """Render a full flight result as a Rich Panel."""
    lines = []

    lines.append(f"[bold]Scenario:[/bold] {name}")
    lines.append(
        f"[bold]Frames:[/bold] {len(result.frames):,}    "
        f"[bold]Duration:[/bold] {result.flight_duration_s:.1f}s    "
        f"[bold]Errors:[/bold] {len(result.error_frames())}"
    )
    lines.append("")

    lines.append(
        f"  Max Altitude:  [bold]{result.max_altitude:>8.1f}[/bold] m AGL"
    )
    lines.append(
        f"  Max Velocity:  [bold]{result.max_velocity:>8.1f}[/bold] m/s"
    )

    min_alt = min((f['alt_filtered'] for f in result.frames), default=0.0)
    if min_alt < -1.0:
        lines.append(
            f"  Min Altitude:  [bold red]{min_alt:>8.1f}[/bold red] m AGL"
        )
    lines.append("")

    alts = [f['alt_filtered'] for f in result.frames if not f['is_error']]
    if alts:
        spark = sparkline(alts, width=56)
        lines.append(f"  [dim]Alt[/dim] {spark}")
        lines.append("")

    vels = [f['vel_filtered'] for f in result.frames if not f['is_error']]
    if vels:
        spark = sparkline(vels, width=56)
        lines.append(f"  [dim]Vel[/dim] {spark}")
        lines.append("")

    lines.append("[bold]State Transitions:[/bold]")
    states_line = ""
    for i, s in enumerate(result.states_visited):
        if i > 0:
            states_line += " [dim]→[/dim] "
        states_line += state_badge(s)
    lines.append(f"  {states_line}")
    lines.append("")

    if result.transitions:
        for ms, from_st, to_st in result.transitions:
            t = ms / 1000.0
            lines.append(
                f"  T+{t:>7.2f}s  {STATE_NAMES[from_st]:>7s} → {STATE_NAMES[to_st]}"
            )
    lines.append("")

    sample = next((f for f in result.frames if not f['is_error']), None)
    if sample and sample['v_3v3_mv'] != 3300:
        lines.append("[bold]Voltages:[/bold]")
        lines.append(
            f"  3V3={sample['v_3v3_mv']}mV  "
            f"5V={sample['v_5v_mv']}mV  "
            f"9V={sample['v_9v_mv']}mV"
        )
        lines.append("")

    errors = result.error_frames()
    if errors:
        lines.append(f"[bold yellow]Error Frames:[/bold yellow] {len(errors)}")
        first_err = errors[0]['timestamp_ms'] / 1000.0
        last_err = errors[-1]['timestamp_ms'] / 1000.0
        lines.append(f"  First at T+{first_err:.2f}s, last at T+{last_err:.2f}s")
        lines.append("")

    if result.bin_path:
        size = os.path.getsize(result.bin_path)
        lines.append(f"[bold]Binary Log:[/bold] {result.bin_path}")
        lines.append(f"  Size: {size:,} bytes ({size / 1024:.1f} KB)")
        lines.append("")

    body = "\n".join(lines)
    return Panel(
        body,
        title="[bold white] FLIGHT RESULT [/bold white]",
        border_style="green" if result.reached_state(LANDED) else "yellow",
        padding=(1, 2),
    )


def render_menu():
    """Render the scenario selection menu."""
    lines = []
    lines.append("")
    lines.append("  Select a scenario to simulate, or:")
    lines.append(
        "  [bold][T][/bold] Run all tests    "
        "[bold][Q][/bold] Quit"
    )
    lines.append("")

    left = []
    right = []
    half = (len(SCENARIOS) + 1) // 2
    for i, s in enumerate(SCENARIOS):
        entry = f"  [bold cyan][{s['key'].upper()}][/bold cyan] {s['name']}"
        if i < half:
            left.append(entry)
        else:
            right.append(entry)

    while len(right) < len(left):
        right.append("")

    for l, r in zip(left, right):
        lines.append(f"{l:<38s}{r}")

    lines.append("")

    body = "\n".join(lines)
    return Panel(
        body,
        title="[bold white] UNSW ROCKETRY — FLIGHT SIMULATOR [/bold white]",
        border_style="blue",
        padding=(1, 1),
    )


def run_scenario(scenario):
    """Run a single scenario and display results."""
    name = scenario['name']
    console.print(f"\n  [bold yellow]Running:[/bold yellow] {name}...")
    console.print(f"  [dim]{scenario['desc']}[/dim]")

    t0 = time.monotonic()
    result = scenario['run']()
    elapsed = time.monotonic() - t0

    console.print(f"  [dim]Completed in {elapsed:.2f}s[/dim]\n")
    console.print(render_result(name, result))

    return result


# ── Test runner (two-column: log left, detail right) ─────────

def _build_detail_lines(test_id, passed, longrepr=None):
    """Build detail panel content for the most recent test result."""
    lines = []
    if passed:
        # Extract class and test name
        parts = test_id.split("::")
        cls = parts[1] if len(parts) > 2 else ""
        test = parts[-1]
        lines.append(f"[bold green]PASSED[/bold green]")
        lines.append("")
        if cls:
            lines.append(f"[bold]{cls}[/bold]")
        lines.append(f"  {test}")
    else:
        parts = test_id.split("::")
        cls = parts[1] if len(parts) > 2 else ""
        test = parts[-1]
        lines.append(f"[bold red]FAILED[/bold red]")
        lines.append("")
        if cls:
            lines.append(f"[bold]{cls}[/bold]")
        lines.append(f"  {test}")
        lines.append("")
        if longrepr:
            tb_lines = longrepr.splitlines()
            # Show the most useful parts: last assertion + context
            for tl in tb_lines:
                # Indent and dim the traceback lines
                stripped = tl.rstrip()
                if not stripped:
                    continue
                if stripped.startswith("E "):
                    # Assertion error line — highlight
                    lines.append(f"[red]{stripped}[/red]")
                elif stripped.startswith(">"):
                    # The failing line of code
                    lines.append(f"[yellow]{stripped}[/yellow]")
                elif "assert" in stripped.lower() or "Error" in stripped:
                    lines.append(f"[red]{stripped}[/red]")
                else:
                    lines.append(f"[dim]{stripped}[/dim]")
    return lines


try:
    from test_meta import ALL as TEST_META, CATEGORY_COLORS
except ImportError:
    TEST_META = {}
    CATEGORY_COLORS = {}

import config as _config


def _export_results(all_results, passed_count, failed_count):
    """Export test results to ~/Desktop as a text report."""
    import datetime
    desktop = os.path.expanduser("~/Desktop")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(desktop, f"test_results_{ts}.txt")

    lines = []
    lines.append("MPR Altitude Logger — Test Results")
    lines.append(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Summary: {passed_count} passed, {failed_count} failed")
    lines.append("=" * 70)
    lines.append("")

    current_cls = None
    for entry in all_results:
        test_id, passed, longrepr, duration, stdout, sim_data = entry
        pieces = test_id.split("::")
        cls = pieces[1] if len(pieces) > 2 else ""
        test = pieces[-1]
        meta = TEST_META.get(test, {})

        if cls and cls != current_cls:
            current_cls = cls
            lines.append(f"\n{cls}")
            lines.append("-" * 60)

        status = "PASS" if passed else "FAIL"
        dur = f"{duration:.3f}s" if duration and duration >= 0.01 else "<10ms"
        lines.append(f"  [{status}] {test}  ({dur})")

        desc = meta.get("desc", "")
        if desc:
            lines.append(f"         {desc}")

        criteria = meta.get("criteria", [])
        if criteria:
            for c in criteria:
                check = "✓" if passed else "✗"
                lines.append(f"         {check} {c}")

        if sim_data:
            lines.append(f"         Flight: {sim_data['frames']} frames, "
                         f"{sim_data['duration']:.1f}s, "
                         f"apogee {sim_data['max_alt']:.1f}m, "
                         f"max vel {sim_data['max_vel']:.1f} m/s, "
                         f"{sim_data['errors']} errors")
            state_names = [STATE_NAMES[s] for s in sim_data['states_visited']]
            lines.append(f"         States: {' → '.join(state_names)}")

        if not passed and longrepr:
            lines.append("")
            for tl in longrepr.splitlines():
                stripped = tl.rstrip()
                if stripped:
                    lines.append(f"         {stripped}")
            lines.append("")

    with open(path, 'w') as f:
        f.write("\n".join(lines) + "\n")
    return path


def browse_results(all_results, passed_count, failed_count):
    """Interactive two-column results browser. Returns 'back' or 'quit'."""
    from rich.layout import Layout

    # Build flat list of selectable items
    items = []
    for entry in all_results:
        test_id, passed, longrepr, duration, stdout, sim_data = entry
        pieces = test_id.split("::")
        cls = pieces[1] if len(pieces) > 2 else ""
        test = pieces[-1]
        items.append((test, test_id, passed, longrepr, cls, duration, stdout, sim_data))

    if not items:
        return 'back'

    cursor = 0
    export_msg = None  # flash message after export

    def render_test_list(max_lines):
        """Render left panel: test list with cursor and class headers."""
        lines = []
        current_cls = None
        item_to_line = {}

        for i, (test, test_id, passed, longrepr, cls, duration, stdout, sim_data) in enumerate(items):
            if cls and cls != current_cls:
                current_cls = cls
                lines.append(f"  [bold cyan]{cls}[/bold cyan]")
            item_to_line[i] = len(lines)
            marker = "▸" if i == cursor else " "
            color = "green" if passed else "red"
            status = "PASS" if passed else "FAIL"
            if i == cursor:
                lines.append(f"  [bold]{marker} [{color}]{status}[/{color}]  {test}[/bold]")
            else:
                lines.append(f"  {marker} [{color}]{status}[/{color}]  [dim]{test}[/dim]")

        # Scroll window to keep cursor visible
        cursor_line = item_to_line.get(cursor, 0)
        half = max_lines // 2
        start = max(0, cursor_line - half)
        end = start + max_lines
        if end > len(lines):
            end = len(lines)
            start = max(0, end - max_lines)
        visible = lines[start:end]
        return "\n".join(visible) if visible else "[dim]No tests[/dim]"

    def render_detail(max_lines):
        """Render right panel: rich detail for selected test."""
        test, test_id, passed, longrepr, cls, duration, stdout, sim_data = items[cursor]
        meta = TEST_META.get(test, {})
        lines = []

        # ── Status badge + category ──
        category = meta.get("category", "")
        cat_color = CATEGORY_COLORS.get(category, "white")
        status_badge = "[bold green]PASSED[/bold green]" if passed else "[bold red]FAILED[/bold red]"
        if category:
            lines.append(f"{status_badge}  [{cat_color}][{category}][/{cat_color}]")
        else:
            lines.append(status_badge)
        lines.append("")

        # ── Test identity ──
        if cls:
            lines.append(f"[bold cyan]{cls}[/bold cyan]")
        lines.append(f"  {test}")

        # Tags
        tags = meta.get("tags", [])
        if tags:
            tag_str = "  ".join(f"[dim]#{t}[/dim]" for t in tags)
            lines.append(f"  {tag_str}")
        lines.append("")

        # ── Description ──
        desc = meta.get("desc", "")
        if desc:
            lines.append(f"[bold]Description[/bold]")
            lines.append(f"  {desc}")
            lines.append("")

        # ── Pass Criteria ──
        criteria = meta.get("criteria", [])
        if criteria:
            lines.append(f"[bold]Pass Criteria[/bold]")
            for c in criteria:
                if passed:
                    lines.append(f"  [green]✓[/green] {c}")
                else:
                    lines.append(f"  [red]✗[/red] {c}")
            lines.append("")

        # ── Scenario details ──
        scenario = meta.get("scenario", {})
        if scenario:
            lines.append(f"[bold]Scenario[/bold]")
            for k, v in scenario.items():
                lines.append(f"  [dim]{k}:[/dim] {v}")
            lines.append("")

        # ── Duration ──
        if duration is not None:
            if duration < 0.01:
                lines.append(f"[bold]Duration:[/bold] <10ms")
            else:
                lines.append(f"[bold]Duration:[/bold] {duration:.3f}s")
            lines.append("")

        # ── Config values ──
        config_keys = meta.get("config_keys", [])
        if config_keys:
            lines.append(f"[bold]Config Values[/bold]")
            for key in config_keys:
                val = getattr(_config, key, "?")
                lines.append(f"  [dim]{key}[/dim] = {val}")
            lines.append("")

        # ── Flight data (from SimResult) ──
        if sim_data:
            lines.append(f"[bold]Flight Data[/bold]")
            lines.append(
                f"  Frames: {sim_data['frames']:,}  "
                f"Duration: {sim_data['duration']:.1f}s  "
                f"Errors: {sim_data['errors']}"
            )
            lines.append(
                f"  Apogee: [bold]{sim_data['max_alt']:.1f}[/bold] m  "
                f"Max Vel: [bold]{sim_data['max_vel']:.1f}[/bold] m/s"
            )
            lines.append("")

            # State transitions
            if sim_data['transitions']:
                lines.append(f"[bold]State Transitions[/bold]")
                states_line = ""
                for i_s, s in enumerate(sim_data['states_visited']):
                    if i_s > 0:
                        states_line += " [dim]→[/dim] "
                    color = STATE_COLORS.get(s, "white")
                    name = STATE_NAMES[s] if s < len(STATE_NAMES) else "?"
                    states_line += f"[bold {color}]{name}[/bold {color}]"
                lines.append(f"  {states_line}")
                for ms, from_st, to_st in sim_data['transitions']:
                    t = ms / 1000.0
                    lines.append(
                        f"  T+{t:>7.2f}s  {STATE_NAMES[from_st]:>7s} → {STATE_NAMES[to_st]}"
                    )
                lines.append("")

            # Altitude sparkline
            if sim_data.get('alt_spark'):
                lines.append(f"[bold]Altitude Profile[/bold]")
                lines.append(f"  [dim]Alt[/dim] {sim_data['alt_spark']}")
                min_alt = sim_data.get('min_alt', 0)
                lines.append(
                    f"  [dim]0m{'':─<20s}{sim_data['max_alt']:.0f}m[/dim]"
                    + (f"  [dim](min: {min_alt:.1f}m)[/dim]" if min_alt < -1 else "")
                )
                lines.append("")

            # Velocity sparkline
            if sim_data.get('vel_spark'):
                lines.append(f"[bold]Velocity Profile[/bold]")
                lines.append(f"  [dim]Vel[/dim] {sim_data['vel_spark']}")
                lines.append(
                    f"  [dim]{sim_data.get('min_vel', 0):.0f}{'':─<18s}"
                    f"{sim_data['max_vel']:.0f} m/s[/dim]"
                )
                lines.append("")

        # ── Source location ──
        file_part = test_id.split("::")[0] if "::" in test_id else ""
        if file_part:
            lines.append(f"[bold]File:[/bold] [dim]{file_part}[/dim]")
            lines.append("")

        # ── Captured stdout ──
        if stdout and stdout.strip():
            lines.append("[bold]Captured Output[/bold]")
            for ol in stdout.strip().splitlines()[:15]:
                lines.append(f"  [dim]{ol.rstrip()}[/dim]")
            if len(stdout.strip().splitlines()) > 15:
                lines.append(f"  [dim]... ({len(stdout.strip().splitlines()) - 15} more lines)[/dim]")
            lines.append("")

        # ── Failure traceback ──
        if not passed and longrepr:
            lines.append("[bold]Traceback[/bold]")
            for tl in longrepr.splitlines():
                stripped = tl.rstrip()
                if not stripped:
                    continue
                if stripped.startswith("E "):
                    lines.append(f"[red]{stripped}[/red]")
                elif stripped.startswith(">"):
                    lines.append(f"[yellow]{stripped}[/yellow]")
                elif "assert" in stripped.lower() or "Error" in stripped:
                    lines.append(f"[red]{stripped}[/red]")
                else:
                    lines.append(f"[dim]{stripped}[/dim]")

        visible = lines[:max_lines]
        return "\n".join(visible) if visible else ""

    def draw():
        term_h = console.size.height
        # Panel borders (top + bottom = 2 lines) + bar (1) + padding rows (0,1 = 0)
        # Total chrome = 3 lines, so content gets term_h - 3
        max_lines = max(5, term_h - 3)

        summary_parts = []
        if passed_count:
            summary_parts.append(f"[green]{passed_count} passed[/green]")
        if failed_count:
            summary_parts.append(f"[red]{failed_count} failed[/red]")
        summary = ", ".join(summary_parts)
        if export_msg:
            bar = f"  {summary}  │  [bold green]{export_msg}[/bold green]"
        else:
            bar = f"  {summary}  │  ↑/↓: navigate  │  E: export  │  B: back  │  Q: quit"

        layout = Layout(size=term_h)
        layout.split_column(
            Layout(name="main"),
            Layout(bar, name="bar", size=1),
        )
        layout["main"].split_row(
            Layout(Panel(
                render_test_list(max_lines),
                title="[bold] Test List [/bold]",
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

        # Render to string, strip any trailing newline, write with cursor positioning
        with console.capture() as cap:
            console.print(layout)
        output = cap.get().rstrip("\n")
        sys.stdout.write("\033[H\033[?25l" + output + "\033[J")
        sys.stdout.flush()

    # Enter alternate screen buffer, hide cursor
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        draw()
        while True:
            key = _getch()
            export_msg = None  # clear flash message on any key
            if key in ('w', 'up'):
                cursor = max(0, cursor - 1)
            elif key in ('s', 'down'):
                cursor = min(len(items) - 1, cursor + 1)
            elif key == 'e':
                path = _export_results(all_results, passed_count, failed_count)
                export_msg = f"Exported to {os.path.basename(path)}"
            elif key == 'b':
                return 'back'
            elif key in ('q',):
                return 'quit'
            elif key is None:
                continue
            else:
                continue
            draw()
    finally:
        # Show cursor, leave alternate screen buffer
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def run_pytest():
    """Run tests with two-column live display: test log left, detail right."""
    import pytest
    from rich.live import Live
    from rich.progress import (
        Progress, BarColumn, TextColumn, MofNCompleteColumn,
        TimeElapsedColumn, SpinnerColumn,
    )
    from rich.layout import Layout
    from rich.columns import Columns

    test_dir = os.path.join(_project_root, 'tests')

    class _TUIProgressPlugin:
        """Two-column test runner: scrolling log + detail panel."""

        def __init__(self):
            self.total = 0
            self.passed = 0
            self.failed = 0
            self.log_lines = []
            self.detail_lines = ["[dim]Waiting for first test...[/dim]"]
            self.all_results = []  # (test_id, passed, longrepr, duration, stdout, sim_data)
            self.current_class = None
            self._live = None
            self._progress = None
            self._task = None

        def _make_progress(self):
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold]{task.fields[status]:45s}", justify="left"),
                BarColumn(bar_width=40, complete_style="green", finished_style="green"),
                MofNCompleteColumn(),
                TextColumn("[green]{task.fields[passed]} passed"),
                TextColumn("[red]{task.fields[failed]} failed"),
                TimeElapsedColumn(),
            )
            self._task = self._progress.add_task(
                "Testing", total=self.total,
                status="Starting...", passed=0, failed=0,
            )

        def _build_display(self):
            """Two-column layout: test log (left) + detail (right), progress bar bottom."""
            term_h = console.size.height
            max_log_lines = max(5, term_h - 6)

            # Left panel: scrolling test log
            visible = self.log_lines[-max_log_lines:]
            log_text = "\n".join(visible) if visible else "[dim]Waiting...[/dim]"

            # Right panel: detail view (current/latest test info)
            detail_visible = self.detail_lines[-(max_log_lines):]
            detail_text = "\n".join(detail_visible)

            layout = Layout()
            layout.split_column(
                Layout(name="main"),
                Layout(self._progress, name="bar", size=1),
            )
            layout["main"].split_row(
                Layout(Panel(
                    log_text,
                    title="[bold] Test Log [/bold]",
                    border_style="blue",
                    padding=(0, 1),
                ), name="log"),
                Layout(Panel(
                    detail_text,
                    title="[bold] Detail [/bold]",
                    border_style="cyan",
                    padding=(0, 1),
                ), name="detail", ratio=1),
            )
            layout["main"]["log"].ratio = 1
            return layout

        def pytest_collection_modifyitems(self, items):
            self.total = len(items)

        def pytest_runtestloop(self, session):
            if session.testsfailed and not session.config.option.continue_on_collection_errors:
                raise session.Interrupted(
                    "%d error%s during collection"
                    % (session.testsfailed, "s" if session.testsfailed != 1 else "")
                )
            if session.config.option.collectonly:
                return True

            self._make_progress()
            self._live = Live(
                self._build_display(),
                console=console,
                refresh_per_second=8,
                transient=False,
            )

            with self._live:
                for i, item in enumerate(session.items):
                    short = item.nodeid.split("/")[-1] if "/" in item.nodeid else item.nodeid
                    if len(short) > 45:
                        short = short[:42] + "..."
                    self._progress.update(self._task, status=short)
                    self._live.update(self._build_display())

                    nextitem = session.items[i + 1] if i + 1 < len(session.items) else None
                    item.config.hook.pytest_runtest_protocol(item=item, nextitem=nextitem)

                    if session.config.option.maxfail:
                        if session.testsfailed >= session.config.option.maxfail:
                            self._progress.update(self._task, status="[red]Stopped on failure")
                            raise session.Interrupted(
                                "stopping after %d failures" % session.testsfailed
                            )

                    self._progress.advance(self._task)
                    self._live.update(self._build_display())
                    time.sleep(0.15)

                status = (
                    f"[bold green]Done! {self.passed} passed"
                    if self.failed == 0
                    else f"[bold yellow]Done. {self.passed} passed, {self.failed} failed"
                )
                self._progress.update(self._task, status=status)
                self._live.update(self._build_display())

            return True

        def pytest_runtest_logreport(self, report):
            if report.when == "call":
                parts = report.nodeid.split("/")[-1] if "/" in report.nodeid else report.nodeid
                if "::" in parts:
                    pieces = parts.split("::")
                    cls = pieces[1] if len(pieces) > 2 else ""
                    test = pieces[-1]
                else:
                    cls = ""
                    test = parts

                if cls and cls != self.current_class:
                    self.current_class = cls
                    self.log_lines.append(f"  [bold cyan]{cls}[/bold cyan]")

                duration = report.duration
                stdout = report.capstdout if hasattr(report, 'capstdout') else ""

                # Capture SimResult if available
                sim_data = None
                try:
                    from sim_harness import _last_result
                    if _last_result is not None:
                        r = _last_result
                        alts = [f['alt_filtered'] for f in r.frames if not f['is_error']]
                        vels = [f['vel_filtered'] for f in r.frames if not f['is_error']]
                        sim_data = {
                            'frames': len(r.frames),
                            'duration': r.flight_duration_s,
                            'max_alt': r.max_altitude,
                            'max_vel': r.max_velocity,
                            'min_alt': min(alts) if alts else 0,
                            'min_vel': min(vels) if vels else 0,
                            'errors': len(r.error_frames()),
                            'states_visited': r.states_visited,
                            'transitions': r.transitions,
                            'alt_spark': sparkline(alts, width=40) if alts else "",
                            'vel_spark': sparkline(vels, width=40) if vels else "",
                        }
                        # Clear for next test
                        import sim_harness
                        sim_harness._last_result = None
                except (ImportError, Exception):
                    pass

                if report.passed:
                    self.passed += 1
                    self.log_lines.append(f"    [green]PASS[/green]  {test}")
                    self.detail_lines = _build_detail_lines(parts, passed=True)
                    self.all_results.append((parts, True, None, duration, stdout, sim_data))
                elif report.failed:
                    self.failed += 1
                    self.log_lines.append(f"    [red]FAIL[/red]  {test}")
                    longrepr = str(report.longrepr) if report.longrepr else ""
                    self.detail_lines = _build_detail_lines(parts, passed=False, longrepr=longrepr)
                    self.all_results.append((parts, False, longrepr, duration, stdout, sim_data))

                if self._progress and self._task is not None:
                    self._progress.update(
                        self._task, passed=self.passed, failed=self.failed,
                    )
                if self._live:
                    self._live.update(self._build_display())

    console.print()
    plugin = _TUIProgressPlugin()
    old_argv = sys.argv
    sys.argv = ['pytest']
    try:
        exit_code = pytest.main(
            [test_dir, '--override-ini=addopts=', '-p', 'no:terminalreporter', '--no-header', '--tb=long'],
            plugins=[plugin],
        )
    finally:
        sys.argv = old_argv

    # Enter interactive results browser
    action = browse_results(plugin.all_results, plugin.passed, plugin.failed)
    return action


# ── Single-key input ──────────────────────────────────────────

def _getch():
    """Read a single keypress. Returns str for normal keys, named strings for special keys."""
    if not sys.stdin.isatty():
        ch = sys.stdin.read(1)
        return ch if ch else 'q'
    import tty
    import termios
    import select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        # Use os.read (unbuffered) so select.select stays in sync with the fd
        ch = os.read(fd, 1)
        if ch == b'\x1b':
            if select.select([fd], [], [], 0.1)[0]:
                ch2 = os.read(fd, 1)
                if ch2 == b'[':
                    if select.select([fd], [], [], 0.1)[0]:
                        ch3 = os.read(fd, 1)
                        if ch3 == b'A': return 'up'
                        if ch3 == b'B': return 'down'
                        if ch3 == b'C': return 'right'
                        if ch3 == b'D': return 'left'
                        # Consume any remaining bytes
                        while select.select([fd], [], [], 0.05)[0]:
                            os.read(fd, 1)
            return None  # bare escape
        if ch == b'\x03':  # Ctrl+C
            return 'q'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch.decode('utf-8', errors='replace')


# ── Main loop ────────────────────────────────────────────────

def main():
    console.print(render_menu())

    while True:
        console.print("  [bold]Press a key:[/bold] ", end="")
        try:
            choice = _getch()
        except (KeyboardInterrupt, EOFError):
            console.print()
            break

        if choice is None or choice in ('up', 'down', 'left', 'right'):
            # Escape sequence or arrow keys — ignore in menu
            console.print()
            continue

        choice = choice.lower()
        console.print(choice)

        if choice == 'q':
            break
        elif choice == 't':
            action = run_pytest()
            if action == 'quit':
                break
            console.print(render_menu())
        else:
            scenario = next((s for s in SCENARIOS if s['key'] == choice), None)
            if scenario:
                run_scenario(scenario)
            else:
                console.print(f"  [red]Unknown: {repr(choice)}[/red]")
                console.print(render_menu())


if __name__ == '__main__':
    main()
