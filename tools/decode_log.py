#!/usr/bin/env python3
"""
Post-flight log decoder — converts binary flight log to CSV and plots.

Usage:
    python decode_log.py flight.bin                  # → flight.csv
    python decode_log.py flight.bin --plot            # → CSV + matplotlib plots
    python decode_log.py flight.bin -o output.csv     # custom output name
"""

import struct
import sys
import argparse
import csv
from pathlib import Path


FRAME_HEADER = b'\xAA\x55'
FRAME_FORMAT = '<IB f f f f f H B'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)
FILE_HEADER_SIZE = 10  # 6 magic + 2 version + 2 frame_size

STATE_NAMES = {0: "PAD", 1: "BOOST", 2: "COAST", 3: "APOGEE",
               4: "DROGUE", 5: "MAIN", 6: "LANDED"}

FIELD_NAMES = [
    "timestamp_ms", "state", "pressure_pa", "temperature_c",
    "alt_raw_m", "alt_filtered_m", "vel_filtered_ms",
    "v_batt_mv", "flags"
]


def decode_flags(flags):
    parts = []
    if flags & 0x01: parts.append("ARMED")
    if flags & 0x02: parts.append("DROGUE_FIRED")
    if flags & 0x04: parts.append("MAIN_FIRED")
    if flags & 0x08: parts.append("ERROR")
    return "|".join(parts) if parts else "SAFE"


def decode_file(filepath):
    """Decode binary log file, yielding dicts per frame."""
    data = Path(filepath).read_bytes()
    
    # Validate header
    if data[:6] != b'RKTLOG':
        print(f"Warning: missing file header, attempting raw decode")
        offset = 0
    else:
        version, fsize = struct.unpack_from('<HH', data, 6)
        print(f"Log version: {version}, frame size: {fsize}")
        offset = FILE_HEADER_SIZE

    frames = []
    skipped = 0

    while offset < len(data) - (2 + FRAME_SIZE):
        # Look for sync header
        if data[offset:offset+2] != FRAME_HEADER:
            offset += 1
            skipped += 1
            continue

        offset += 2  # skip header bytes

        if offset + FRAME_SIZE > len(data):
            break

        values = struct.unpack_from(FRAME_FORMAT, data, offset)
        offset += FRAME_SIZE

        frame = dict(zip(FIELD_NAMES, values))
        frame["state_name"] = STATE_NAMES.get(frame["state"], "UNKNOWN")
        frame["flags_str"] = decode_flags(frame["flags"])
        frames.append(frame)

    if skipped:
        print(f"Skipped {skipped} bytes looking for frame sync")

    return frames


def to_csv(frames, output_path):
    """Write decoded frames to CSV."""
    if not frames:
        print("No frames to write!")
        return

    fields = FIELD_NAMES + ["state_name", "flags_str"]
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(frames)

    print(f"Wrote {len(frames)} frames to {output_path}")


def plot_flight(frames):
    """Generate matplotlib flight summary plots."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("Install matplotlib+numpy for plots: pip install matplotlib numpy")
        return

    t = np.array([f["timestamp_ms"] / 1000.0 for f in frames])
    t -= t[0]  # zero-reference time

    alt_raw = np.array([f["alt_raw_m"] for f in frames])
    alt_filt = np.array([f["alt_filtered_m"] for f in frames])
    vel = np.array([f["vel_filtered_ms"] for f in frames])
    pressure = np.array([f["pressure_pa"] for f in frames])
    batt = np.array([f["v_batt_mv"] for f in frames])
    states = np.array([f["state"] for f in frames])

    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    fig.suptitle("Flight Data", fontsize=14, fontweight="bold")

    # Altitude
    ax = axes[0]
    ax.plot(t, alt_raw, alpha=0.3, label="Raw baro", color="gray")
    ax.plot(t, alt_filt, label="Kalman filtered", color="blue", linewidth=1.5)
    ax.set_ylabel("Altitude AGL (m)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Mark state transitions
    for i in range(1, len(states)):
        if states[i] != states[i-1]:
            ax.axvline(t[i], color="red", alpha=0.5, linestyle="--")
            ax.annotate(STATE_NAMES.get(states[i], "?"),
                       (t[i], alt_filt[i]), fontsize=8,
                       rotation=90, va="bottom")

    # Velocity
    ax = axes[1]
    ax.plot(t, vel, color="green", linewidth=1.5)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_ylabel("Velocity (m/s)")
    ax.grid(True, alpha=0.3)

    # Pressure
    ax = axes[2]
    ax.plot(t, pressure / 100, color="orange")  # hPa
    ax.set_ylabel("Pressure (hPa)")
    ax.grid(True, alpha=0.3)

    # Battery
    ax = axes[3]
    ax.plot(t, batt / 1000, color="red")
    ax.set_ylabel("Battery (V)")
    ax.set_xlabel("Time (s)")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    out_path = "flight_plot.png"
    plt.savefig(out_path, dpi=150)
    print(f"Saved plot: {out_path}")
    plt.show()


def print_summary(frames):
    """Print flight statistics."""
    if not frames:
        return

    max_alt = max(f["alt_filtered_m"] for f in frames)
    max_vel = max(f["vel_filtered_ms"] for f in frames)
    duration = (frames[-1]["timestamp_ms"] - frames[0]["timestamp_ms"]) / 1000

    # Find state transitions
    transitions = []
    for i in range(1, len(frames)):
        if frames[i]["state"] != frames[i-1]["state"]:
            t = (frames[i]["timestamp_ms"] - frames[0]["timestamp_ms"]) / 1000
            transitions.append((t, STATE_NAMES.get(frames[i]["state"], "?")))

    print("\n═══════════════════════════════════════")
    print("         FLIGHT SUMMARY")
    print("═══════════════════════════════════════")
    print(f"  Duration:      {duration:.1f} s")
    print(f"  Max altitude:  {max_alt:.1f} m AGL")
    print(f"  Max velocity:  {max_vel:.1f} m/s")
    print(f"  Total frames:  {len(frames)}")
    print(f"  Avg rate:      {len(frames)/duration:.1f} Hz")
    print()
    print("  State transitions:")
    for t, name in transitions:
        print(f"    T+{t:6.2f}s → {name}")
    print("═══════════════════════════════════════\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Decode rocket flight log")
    parser.add_argument("logfile", help="Binary log file (.bin)")
    parser.add_argument("-o", "--output", help="Output CSV path")
    parser.add_argument("--plot", action="store_true", help="Generate plots")
    args = parser.parse_args()

    frames = decode_file(args.logfile)
    print_summary(frames)

    out = args.output or Path(args.logfile).with_suffix('.csv')
    to_csv(frames, out)

    if args.plot:
        plot_flight(frames)
