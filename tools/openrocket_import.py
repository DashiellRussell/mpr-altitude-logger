#!/usr/bin/env python3
"""
OpenRocket → Avionics pipeline.

Imports OpenRocket simulation CSV exports and converts them into the format
our flight review dashboard expects, so you can overlay predicted vs actual.

Also imports RASP .eng thrust curve files from ThrustCurve.org.

═══════════════════════════════════════════════════════════════════
HOW TO EXPORT FROM OPENROCKET:
═══════════════════════════════════════════════════════════════════

1. Open your .ork file in OpenRocket
2. Run your simulation (Flight Simulations tab → click sim → Run)
3. Click "Plot / Export"
4. Go to the "Export data" tab
5. Select these fields (minimum):
     ☑ Time
     ☑ Altitude
     ☑ Vertical velocity
     ☑ Total velocity
     ☑ Vertical acceleration
     ☑ Mach number
     ☑ Thrust
     ☑ Drag force
     ☑ Mass
     ☑ Air pressure
     ☑ Air temperature
   (You can tick more — the importer ignores unknowns)
6. ☑ Include flight events in comments
7. Field separator: Comma
8. Export → save as e.g. "sim_export.csv"

═══════════════════════════════════════════════════════════════════

Usage:
    # Convert OpenRocket CSV to dashboard-compatible format
    python openrocket_import.py sim_export.csv -o sim_predicted.csv

    # Also generate JSON for programmatic use
    python openrocket_import.py sim_export.csv -o sim_predicted.csv --json

    # Import a .eng thrust curve and print motor stats
    python openrocket_import.py --eng-info H100.eng

    # Run full sim using .eng file + OpenRocket mass/cd/diameter
    python openrocket_import.py sim_export.csv --extract-params
    # (prints the motor/rocket params OR detected from the export)
"""

import csv
import json
import sys
import re
import argparse
from pathlib import Path


# ── OpenRocket CSV Column Name Mapping ────────────────────────
# OpenRocket column headers vary by version and locale.
# This maps known header patterns → our internal field names.

COLUMN_MAP = {
    # Time
    "time": "time_s",
    "# time": "time_s",

    # Altitude
    "altitude": "altitude_m",
    "height": "altitude_m",

    # Velocity
    "vertical velocity": "velocity_ms",
    "vertical speed": "velocity_ms",
    "total velocity": "total_velocity_ms",
    "total speed": "total_velocity_ms",
    "lateral velocity": "lateral_velocity_ms",

    # Acceleration
    "vertical acceleration": "acceleration_ms2",
    "total acceleration": "total_acceleration_ms2",

    # Aero
    "mach number": "mach",
    "mach": "mach",
    "drag force": "drag_N",
    "drag coefficient": "cd",
    "thrust": "thrust_N",
    "normal force coefficient": "cn",
    "stability margin calibers": "stability_cal",
    "stability margin": "stability_cal",
    "angle of attack": "aoa_deg",

    # Mass
    "mass": "mass_kg",
    "total mass": "mass_kg",
    "propellant mass": "prop_mass_kg",

    # Atmosphere
    "air pressure": "pressure_pa",
    "atmospheric pressure": "pressure_pa",
    "pressure": "pressure_pa",
    "air temperature": "temperature_c",
    "temperature": "temperature_c",
    "wind speed": "wind_speed_ms",

    # Position
    "lateral distance": "lateral_distance_m",
    "lateral direction": "lateral_direction_deg",
    "position east of launch": "pos_east_m",
    "position north of launch": "pos_north_m",
}


def normalize_header(header):
    """Normalize an OpenRocket column header to our field name."""
    # Strip units: "Altitude (m)" → "altitude"
    clean = re.sub(r'\s*\(.*?\)\s*', '', header).strip().lower()
    # Strip leading # or spaces
    clean = clean.lstrip('# ').strip()
    return COLUMN_MAP.get(clean, None)


def detect_unit_and_convert(header, values):
    """
    Detect units from column header and convert to SI if needed.
    OpenRocket can export in various unit systems.
    """
    unit_match = re.search(r'\(([^)]+)\)', header)
    if not unit_match:
        return values

    unit = unit_match.group(1).strip().lower()

    conversions = {
        # Length
        "ft": lambda v: v * 0.3048,
        "in": lambda v: v * 0.0254,
        "km": lambda v: v * 1000,
        "mi": lambda v: v * 1609.34,

        # Velocity
        "ft/s": lambda v: v * 0.3048,
        "mph": lambda v: v * 0.44704,
        "km/h": lambda v: v * 0.27778,
        "kph": lambda v: v * 0.27778,
        "kn": lambda v: v * 0.51444,

        # Acceleration
        "ft/s²": lambda v: v * 0.3048,
        "ft/s^2": lambda v: v * 0.3048,
        "g": lambda v: v * 9.80665,

        # Force
        "lbf": lambda v: v * 4.44822,
        "kgf": lambda v: v * 9.80665,

        # Mass
        "lb": lambda v: v * 0.453592,
        "oz": lambda v: v * 0.0283495,
        "g": lambda v: v * 0.001,  # grams to kg (for mass columns)

        # Pressure
        "psi": lambda v: v * 6894.76,
        "atm": lambda v: v * 101325,
        "bar": lambda v: v * 100000,
        "mbar": lambda v: v * 100,
        "hpa": lambda v: v * 100,
        "mmhg": lambda v: v * 133.322,

        # Temperature
        "°f": lambda v: (v - 32) * 5 / 9,
        "f": lambda v: (v - 32) * 5 / 9,
        "k": lambda v: v - 273.15,
    }

    converter = conversions.get(unit)
    if converter:
        return [converter(v) if v is not None else None for v in values]
    return values


def parse_openrocket_csv(filepath):
    """
    Parse an OpenRocket CSV export file.
    
    Handles:
    - Comment lines starting with #
    - Flight event annotations in comments
    - Various column orderings
    - Unit detection and conversion to SI
    
    Returns:
        (data_rows, events, metadata)
        data_rows: list of dicts with normalized field names
        events: list of {time_s, event_name}
        metadata: dict of any extracted info
    """
    lines = Path(filepath).read_text(encoding='utf-8-sig').splitlines()

    # Extract comments and find header
    comments = []
    header_line = None
    data_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            comments.append(stripped.lstrip('#').strip())
            continue
        # First non-comment, non-empty line is the header
        if header_line is None:
            header_line = stripped
            data_start = i + 1
            break

    if header_line is None:
        raise ValueError("Could not find header row in CSV")

    # Parse events from comments
    # OpenRocket format: "Event APOGEE occurred at t=12.345 seconds"
    events = []
    for comment in comments:
        event_match = re.search(
            r'Event\s+(\w+)\s+occurred\s+at\s+t\s*=\s*([\d.]+)',
            comment, re.IGNORECASE
        )
        if event_match:
            events.append({
                "event": event_match.group(1).upper(),
                "time_s": float(event_match.group(2)),
            })

    # Parse header
    # Try comma first, then semicolon, then tab
    for sep in [',', ';', '\t']:
        headers = [h.strip() for h in header_line.split(sep)]
        if len(headers) > 1:
            break
    else:
        raise ValueError(f"Cannot detect separator in header: {header_line}")

    # Map headers to our field names
    field_map = {}  # index → (our_field_name, original_header)
    for i, h in enumerate(headers):
        field = normalize_header(h)
        if field:
            field_map[i] = (field, h)

    if "time_s" not in [v[0] for v in field_map.values()]:
        raise ValueError(
            f"No time column found. Headers: {headers}\n"
            f"Mapped: {field_map}"
        )

    # Parse data rows
    raw_columns = {field: [] for field, _ in field_map.values()}

    for line in lines[data_start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        values = stripped.split(sep)
        for i, (field, _) in field_map.items():
            if i < len(values):
                try:
                    raw_columns[field].append(float(values[i]))
                except ValueError:
                    raw_columns[field].append(None)
            else:
                raw_columns[field].append(None)

    # Convert units
    for i, (field, orig_header) in field_map.items():
        raw_columns[field] = detect_unit_and_convert(orig_header, raw_columns[field])

    # Build row dicts
    n_rows = len(raw_columns["time_s"])
    data_rows = []
    for j in range(n_rows):
        row = {}
        for field in raw_columns:
            row[field] = raw_columns[field][j]
        data_rows.append(row)

    # Determine flight state for each row using events
    data_rows = assign_states(data_rows, events)

    metadata = {
        "source": str(filepath),
        "columns_found": list(set(f for f, _ in field_map.values())),
        "columns_raw": headers,
        "n_events": len(events),
        "n_rows": n_rows,
    }

    return data_rows, events, metadata


def assign_states(rows, events):
    """Assign flight state to each row based on OpenRocket events."""
    # OpenRocket events: LAUNCH, BURNOUT, APOGEE, RECOVERY_DEVICE_DEPLOYMENT,
    # GROUND_HIT, EJECTION_CHARGE, etc.
    event_map = {
        "LAUNCH": "BOOST",
        "IGNITION": "BOOST",
        "LIFTOFF": "BOOST",
        "BURNOUT": "COAST",
        "APOGEE": "APOGEE",
        "RECOVERY_DEVICE_DEPLOYMENT": "DROGUE",
        "EJECTION_CHARGE": "DROGUE",
        "GROUND_HIT": "LANDED",
        "SIMULATION_END": "LANDED",
        "TUMBLE": "DROGUE",
    }

    # Build timeline of state changes
    state_changes = []
    for ev in sorted(events, key=lambda e: e["time_s"]):
        mapped = event_map.get(ev["event"])
        if mapped:
            state_changes.append((ev["time_s"], mapped))

    # Assign states
    for row in rows:
        t = row["time_s"]
        state = "PAD"
        for change_t, change_state in state_changes:
            if t >= change_t:
                state = change_state
        row["state"] = state

    return rows


def to_dashboard_csv(rows, output_path):
    """
    Write rows in the format our flight review dashboard expects.
    
    Dashboard expects: time_s, altitude_m, velocity_ms, acceleration_ms2,
                       mach, thrust_N, drag_N, mass_kg, pressure_pa, state
    """
    fields = [
        "time_s", "altitude_m", "velocity_ms", "acceleration_ms2",
        "mach", "thrust_N", "drag_N", "mass_kg", "pressure_pa",
        "air_density", "state",
    ]

    # Only include fields that have data
    active_fields = []
    for f in fields:
        if any(row.get(f) is not None for row in rows):
            active_fields.append(f)

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=active_fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            clean = {k: (f"{v:.6f}" if isinstance(v, float) else v)
                     for k, v in row.items() if k in active_fields}
            writer.writerow(clean)

    print(f"Wrote {len(rows)} rows → {output_path}")
    print(f"Fields: {', '.join(active_fields)}")


def to_json(rows, output_path):
    """Write as JSON for dashboard import."""
    with open(output_path, 'w') as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"Wrote JSON → {output_path}")


def extract_rocket_params(rows, events):
    """
    Attempt to extract rocket parameters from the simulation data.
    Useful for cross-checking against your physical rocket.
    """
    params = {}

    # Mass at launch (first row)
    if rows[0].get("mass_kg") is not None:
        params["launch_mass_kg"] = rows[0]["mass_kg"]

    # Dry mass (mass after burnout)
    burnout_t = None
    for ev in events:
        if ev["event"] in ("BURNOUT",):
            burnout_t = ev["time_s"]
            break

    if burnout_t and rows[0].get("mass_kg") is not None:
        for row in rows:
            if row["time_s"] >= burnout_t:
                params["dry_mass_kg"] = row["mass_kg"]
                params["propellant_mass_kg"] = params["launch_mass_kg"] - row["mass_kg"]
                break

    # Max thrust
    thrusts = [r.get("thrust_N", 0) or 0 for r in rows]
    if max(thrusts) > 0:
        params["max_thrust_N"] = max(thrusts)
        burn_rows = [r for r in rows if (r.get("thrust_N") or 0) > 0.5]
        if burn_rows:
            params["burn_time_s"] = burn_rows[-1]["time_s"] - burn_rows[0]["time_s"]
            total_impulse = sum(
                (burn_rows[i]["thrust_N"] + burn_rows[i+1]["thrust_N"]) / 2 *
                (burn_rows[i+1]["time_s"] - burn_rows[i]["time_s"])
                for i in range(len(burn_rows) - 1)
            )
            params["total_impulse_Ns"] = total_impulse
            params["avg_thrust_N"] = total_impulse / params["burn_time_s"] if params["burn_time_s"] > 0 else 0

    # Peak altitude
    alts = [r.get("altitude_m", 0) or 0 for r in rows]
    params["apogee_m"] = max(alts)

    # Peak velocity & Mach
    vels = [r.get("velocity_ms", 0) or 0 for r in rows]
    params["max_velocity_ms"] = max(vels)

    machs = [r.get("mach", 0) or 0 for r in rows]
    params["max_mach"] = max(machs)

    # Event times
    for ev in events:
        key = f"t_{ev['event'].lower()}_s"
        params[key] = ev["time_s"]

    return params


def parse_eng_file(filepath):
    """
    Parse a RASP .eng motor file.
    
    Format:
        ; comment lines
        MOTOR_NAME diameter(mm) length(mm) delays prop_mass(kg) total_mass(kg) manufacturer
        time1 thrust1
        time2 thrust2
        ...
    """
    points = []
    header = None

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            parts = line.split()
            if header is None:
                # Header line
                header = {
                    "name": parts[0],
                    "diameter_mm": float(parts[1]),
                    "length_mm": float(parts[2]),
                    "delays": parts[3],
                    "prop_mass_kg": float(parts[4]),
                    "total_mass_kg": float(parts[5]),
                    "manufacturer": parts[6] if len(parts) > 6 else "Unknown",
                }
                continue
            if len(parts) >= 2:
                t, thrust = float(parts[0]), float(parts[1])
                points.append((t, thrust))

    if not header:
        raise ValueError(f"No header found in {filepath}")

    # Compute stats
    total_impulse = sum(
        (points[i][1] + points[i+1][1]) / 2 * (points[i+1][0] - points[i][0])
        for i in range(len(points) - 1)
    )
    burn_time = points[-1][0] - points[0][0]
    avg_thrust = total_impulse / burn_time if burn_time > 0 else 0
    peak_thrust = max(t for _, t in points)

    header["total_impulse_Ns"] = total_impulse
    header["burn_time_s"] = burn_time
    header["avg_thrust_N"] = avg_thrust
    header["peak_thrust_N"] = peak_thrust
    header["thrust_curve"] = points

    return header


def print_eng_info(motor):
    """Pretty-print motor stats from .eng file."""
    print(f"\n{'═' * 50}")
    print(f"  Motor: {motor['name']}")
    print(f"  Manufacturer: {motor['manufacturer']}")
    print(f"{'═' * 50}")
    print(f"  Diameter:       {motor['diameter_mm']:.1f} mm")
    print(f"  Length:         {motor['length_mm']:.1f} mm")
    print(f"  Propellant:     {motor['prop_mass_kg'] * 1000:.1f} g")
    print(f"  Total mass:     {motor['total_mass_kg'] * 1000:.1f} g")
    print(f"  Total impulse:  {motor['total_impulse_Ns']:.1f} N·s")
    print(f"  Avg thrust:     {motor['avg_thrust_N']:.1f} N")
    print(f"  Peak thrust:    {motor['peak_thrust_N']:.1f} N")
    print(f"  Burn time:      {motor['burn_time_s']:.2f} s")
    print(f"  Delays:         {motor['delays']}")
    print(f"  Data points:    {len(motor['thrust_curve'])}")
    print(f"{'═' * 50}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Import OpenRocket simulation data for flight review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s sim_export.csv                      → sim_predicted.csv
  %(prog)s sim_export.csv -o my_sim.csv        → my_sim.csv
  %(prog)s sim_export.csv --json               → also outputs .json
  %(prog)s sim_export.csv --extract-params     → print rocket params
  %(prog)s --eng-info Cesaroni_H100.eng        → print motor stats
        """
    )
    parser.add_argument("csvfile", nargs="?", help="OpenRocket CSV export file")
    parser.add_argument("-o", "--output", default="sim_predicted.csv",
                       help="Output CSV path (default: sim_predicted.csv)")
    parser.add_argument("--json", action="store_true",
                       help="Also output JSON")
    parser.add_argument("--extract-params", action="store_true",
                       help="Print extracted rocket parameters")
    parser.add_argument("--eng-info", type=str,
                       help="Print info from a .eng motor file")
    args = parser.parse_args()

    # .eng info mode
    if args.eng_info:
        motor = parse_eng_file(args.eng_info)
        print_eng_info(motor)
        return

    if not args.csvfile:
        parser.error("Provide an OpenRocket CSV file (or use --eng-info)")

    print(f"Parsing: {args.csvfile}")
    rows, events, meta = parse_openrocket_csv(args.csvfile)

    print(f"  Rows: {meta['n_rows']}")
    print(f"  Columns: {', '.join(meta['columns_found'])}")
    print(f"  Events: {meta['n_events']}")

    if events:
        print("\n  Flight events:")
        for ev in events:
            print(f"    T+{ev['time_s']:7.3f}s  {ev['event']}")

    if args.extract_params:
        params = extract_rocket_params(rows, events)
        print("\n  Extracted parameters:")
        for k, v in params.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.4f}")
            else:
                print(f"    {k}: {v}")

    # Write output
    print()
    to_dashboard_csv(rows, args.output)

    if args.json:
        json_path = Path(args.output).with_suffix('.json')
        to_json(rows, json_path)

    print("\nDone! Load this CSV into the flight review dashboard's 'Simulation' upload.")


if __name__ == "__main__":
    main()
