#!/usr/bin/env python3
"""
MPR Altitude Logger — Tool Launcher

Central menu for all avionics tools, grouped by purpose.
Prompts for arguments where needed before launching.

Usage:
    python tools/launch.py
    pnpm launch
"""

import argparse
import os
import sys
import glob
import subprocess

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOLS_DIR)

# ── ANSI helpers ──

BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
CLEAR = "\033[2J\033[H"


def prompt(label, default=None, required=False):
    """Prompt user for a value. Returns None if skipped."""
    suffix = f" [{default}]" if default else ""
    req = " (required)" if required else ""
    try:
        val = input(f"    {DIM}{label}{req}{suffix}:{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not val:
        return default
    return val


def prompt_file(label, pattern="*", required=False):
    """Prompt for a file path with tab-like glob hints."""
    suffix = " (required)" if required else ""
    try:
        val = input(f"    {DIM}{label}{suffix}:{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not val:
        return None
    # Expand ~ and check existence
    val = os.path.expanduser(val)
    if not os.path.exists(val):
        print(f"    {RED}File not found: {val}{RESET}")
        return None
    return val


def prompt_yn(label, default=False):
    """Yes/no prompt."""
    hint = "Y/n" if default else "y/N"
    try:
        val = input(f"    {DIM}{label} [{hint}]:{RESET} ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not val:
        return default
    return val in ("y", "yes")


def prompt_port():
    """Auto-detect serial ports and let user pick one, or auto-detect."""
    ports = []
    # Look for common Pico serial ports
    for pattern in ["/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"]:
        ports.extend(sorted(glob.glob(pattern)))
    if not ports:
        print(f"    {DIM}No serial ports detected — will auto-detect{RESET}")
        return []
    if len(ports) == 1:
        print(f"    {DIM}Detected port: {ports[0]}{RESET}")
        return ["--port", ports[0]]
    print(f"    {DIM}Available ports:{RESET}")
    for i, p in enumerate(ports):
        print(f"      {GREEN}[{i + 1}]{RESET} {p}")
    try:
        val = input(f"    {DIM}Select port [1]:{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return []
    if not val:
        idx = 0
    else:
        try:
            idx = int(val) - 1
        except ValueError:
            return ["--port", val]  # Treat as raw path
    if 0 <= idx < len(ports):
        return ["--port", ports[idx]]
    return []


# ── Tool argument prompting ──

def args_serial():
    """Prompt for serial port (used by live telemetry tools)."""
    return prompt_port()


def args_simulate():
    """Prompt for flight simulation parameters."""
    print(f"    {DIM}Configure simulation parameters (Enter to use defaults):{RESET}")
    args = []
    mass = prompt("Dry mass (kg)", required=True)
    if mass is None:
        return None
    args.extend(["--mass", mass])

    diameter = prompt("Body diameter (m)", required=True)
    if diameter is None:
        return None
    args.extend(["--diameter", diameter])

    motor = prompt("Motor name", default="Cesaroni_H100")
    if motor:
        args.extend(["--motor", motor])

    motor_file = prompt("Motor .eng file (optional)")
    if motor_file:
        args.extend(["--motor-file", os.path.expanduser(motor_file)])

    cd = prompt("Drag coefficient", default="0.45")
    if cd:
        args.extend(["--cd", cd])

    rail = prompt("Launch rail length (m)", default="1.5")
    if rail:
        args.extend(["--rail", rail])

    main_alt = prompt("Main deploy altitude AGL (m)", default="300")
    if main_alt:
        args.extend(["--main-alt", main_alt])

    output = prompt("Output CSV path", default="sim_predicted.csv")
    if output:
        args.extend(["-o", output])

    do_json = prompt_yn("Also output JSON?")
    if do_json:
        args.append("--json")

    return args


def args_seed_flight():
    """Prompt for seed flight parameters."""
    print(f"    {DIM}Configure synthetic flight (Enter to use defaults):{RESET}")
    args = []

    motor = prompt("Motor name", default="Cesaroni_H100")
    if motor:
        args.extend(["--motor", motor])

    mass = prompt("Dry mass (kg)", default="2.5")
    if mass:
        args.extend(["--mass", mass])

    cd = prompt("Drag coefficient", default="0.45")
    if cd:
        args.extend(["--cd", cd])

    diameter = prompt("Body diameter (m)", default="0.054")
    if diameter:
        args.extend(["--diameter", diameter])

    main_alt = prompt("Main deploy altitude (m)", default="300")
    if main_alt:
        args.extend(["--main-alt", main_alt])

    seed = prompt("Random seed", default="42")
    if seed:
        args.extend(["--seed", seed])

    output = prompt("Output .bin path", default="seed_flight.bin")
    if output:
        args.extend(["-o", output])

    to_sd = prompt_yn("Write to SD card?")
    if to_sd:
        args.append("--sd")

    verify = prompt_yn("Verify round-trip after?", default=True)
    if verify:
        args.append("--verify")

    return args


def args_decode_log():
    """Prompt for decode_log parameters."""
    logfile = prompt_file("Path to .bin flight log", required=True)
    if not logfile:
        return None
    args = [logfile]

    output = prompt("Output CSV path (optional)")
    if output:
        args.extend(["-o", output])

    do_plot = prompt_yn("Generate plots?", default=True)
    if do_plot:
        args.append("--plot")

    return args


def args_openrocket():
    """Prompt for OpenRocket import parameters."""
    # First ask what they want to do
    print(f"    {DIM}Options:{RESET}")
    print(f"      {GREEN}[1]{RESET} Import OpenRocket file (.ork or .csv)")
    print(f"      {GREEN}[2]{RESET} View .eng motor file info")
    try:
        mode = input(f"    {DIM}Select [1]:{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None

    if mode == "2":
        eng = prompt_file("Path to .eng motor file", required=True)
        if not eng:
            return None
        return ["--eng-info", eng]

    # Import mode
    infile = prompt_file("Path to .ork or .csv file", required=True)
    if not infile:
        return None
    args = [infile]

    if infile.endswith(".ork"):
        list_first = prompt_yn("List available simulations first?")
        if list_first:
            # Run --list-sims, then re-prompt
            subprocess.run(
                [sys.executable, os.path.join(TOOLS_DIR, "openrocket_import.py"),
                 infile, "--list-sims"],
                cwd=ROOT_DIR,
            )
            sim_idx = prompt("Simulation index to extract", default="0")
            if sim_idx:
                args.extend(["--sim", sim_idx])

    output = prompt("Output CSV path", default="sim_predicted.csv")
    if output:
        args.extend(["-o", output])

    do_json = prompt_yn("Also output JSON?")
    if do_json:
        args.append("--json")

    extract = prompt_yn("Print extracted rocket parameters?")
    if extract:
        args.append("--extract-params")

    return args


def args_postflight():
    """Prompt for post-flight analysis parameters."""
    print(f"    {DIM}Options:{RESET}")
    print(f"      {GREEN}[1]{RESET} Download from Pico over USB")
    print(f"      {GREEN}[2]{RESET} Analyze local .bin file")
    try:
        mode = input(f"    {DIM}Select [1]:{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None

    args = []
    if mode == "2":
        logfile = prompt_file("Path to .bin flight log", required=True)
        if not logfile:
            return None
        args.append(logfile)
    else:
        args.append("--download")
        port_args = prompt_port()
        args.extend(port_args)

    sim = prompt_file("Simulation CSV for comparison (optional)")
    if sim:
        args.extend(["--sim", sim])

    return args


def args_pico_diag():
    """Prompt for Pico diagnostic options."""
    port_args = prompt_port()
    deploy = prompt_yn("Deploy firmware and exit (no interactive menu)?")
    args = list(port_args)
    if deploy:
        args.append("--deploy")
    return args


# ── Tool definitions, grouped by purpose ──

def tool(key, name, desc, script, args_fn=None, aliases=None):
    return {
        "key": key,
        "name": name,
        "desc": desc,
        "script": os.path.join(TOOLS_DIR, script),
        "args_fn": args_fn,
        "aliases": aliases or [],
    }


GROUPS = [
    {
        "name": "Live Telemetry",
        "desc": "Connect to the Pico over USB serial",
        "items": [
            tool("1", "Ground Station", "Live sensor dashboard — barometer, voltages, sparklines", "tui.py", args_serial, ["groundstation", "gs", "tui"]),
            tool("2", "Pre-Flight Checklist", "Step-by-step hardware checks with GO/NO-GO", "preflight.py", args_serial, ["preflight", "pre"]),
            tool("3", "Pico Diagnostics", "Hardware stress tests, firmware deploy", "pico_diag_tui.py", args_pico_diag, ["pico", "diag"]),
        ],
    },
    {
        "name": "Simulation & Testing",
        "desc": "Offline — no Pico required",
        "items": [
            tool("4", "Flight Simulator TUI", "Interactive scenarios through the avionics pipeline", "simulator_tui.py", aliases=["simulator", "sim-tui"]),
            tool("5", "Flight Simulator CLI", "1D Euler sim — generate predicted CSV", "simulate.py", args_simulate, ["simulate", "sim"]),
            tool("6", "Seed Flight Log", "Generate synthetic .bin flight logs for testing", "seed_flight.py", args_seed_flight, ["seed"]),
        ],
    },
    {
        "name": "Post-Flight Analysis",
        "desc": "Review and decode flight data",
        "items": [
            tool("7", "Post-Flight TUI", "Interactive flight review with state timeline", "postflight.py", args_postflight, ["postflight", "post"]),
            tool("8", "Decode Log", "Convert binary .bin to CSV + matplotlib plots", "decode_log.py", args_decode_log, ["decode"]),
            tool("9", "OpenRocket Import", "Convert OpenRocket exports to dashboard format", "openrocket_import.py", args_openrocket, ["openrocket", "ork"]),
        ],
    },
]


# ── Rendering ──

def render_menu():
    lines = []
    lines.append(CLEAR)
    lines.append(f"{BOLD}  MPR Altitude Logger — Tool Launcher{RESET}")
    lines.append(f"  {DIM}UNSW Rocketry Avionics{RESET}")
    lines.append("")

    for group in GROUPS:
        lines.append(f"  {BOLD}{CYAN}{group['name']}{RESET}  {DIM}{group['desc']}{RESET}")
        for item in group["items"]:
            lines.append(
                f"    {GREEN}[{item['key']}]{RESET}  {BOLD}{item['name']}{RESET}"
                f"  {DIM}—{RESET} {item['desc']}"
            )
        lines.append("")

    lines.append(f"  {DIM}[q] Quit{RESET}")
    lines.append("")
    return "\n".join(lines)


def get_item(key):
    """Look up a tool by key number or alias name."""
    for group in GROUPS:
        for item in group["items"]:
            if item["key"] == key or key in item["aliases"]:
                return item
    return None


def all_aliases():
    """Return all valid alias names for --help display."""
    names = []
    for group in GROUPS:
        for item in group["items"]:
            names.extend(item["aliases"])
    return names


def launch_item(item):
    """Prompt for args (if needed) and launch a tool. Returns True if launched."""
    cmd = [sys.executable, item["script"]]

    if item["args_fn"]:
        print(f"\n  {BOLD}{item['name']}{RESET}")
        extra_args = item["args_fn"]()
        if extra_args is None:
            print(f"  {DIM}Cancelled.{RESET}")
            return False
        cmd.extend(extra_args)

    print(f"\n  {DIM}Running: {' '.join(cmd[1:])}{RESET}\n")

    try:
        subprocess.run(cmd, cwd=ROOT_DIR)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print(f"  {RED}Script not found: {item['script']}{RESET}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="MPR Altitude Logger — Tool Launcher",
        epilog=f"Available tool names: {', '.join(all_aliases())}",
    )
    parser.add_argument(
        "tool", nargs="?", default=None,
        help="Jump straight to a tool by name (e.g. preflight, postflight, simulator)",
    )
    args = parser.parse_args()

    # Direct launch mode: pnpm dev:tui -- postflight
    if args.tool:
        item = get_item(args.tool.lower())
        if not item:
            print(f"  {RED}Unknown tool: {args.tool}{RESET}")
            print(f"  {DIM}Available: {', '.join(all_aliases())}{RESET}")
            sys.exit(1)
        launch_item(item)
        return

    # Interactive menu mode
    print(render_menu(), end="")

    while True:
        try:
            choice = input(f"  {YELLOW}>{RESET} ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if choice in ("q", "quit", ""):
            break

        item = get_item(choice)
        if not item:
            print(f"  {RED}Unknown option: {choice}{RESET}")
            continue

        launch_item(item)

        # Re-show menu after returning
        print(render_menu(), end="")


if __name__ == "__main__":
    main()
