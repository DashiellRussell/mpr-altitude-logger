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
import zipfile
import xml.etree.ElementTree as ET
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


def parse_ork_file(filepath, sim_index=0):
    """
    Parse an OpenRocket .ork file directly (ZIP-compressed XML).

    Extracts simulation data without needing to manually export CSV from
    OpenRocket. The .ork file contains full sim results if the sim was run
    before saving.

    Args:
        filepath: Path to .ork file
        sim_index: Which simulation to extract (0-based). Use -1 to list all.

    Returns:
        (data_rows, events, metadata) — same format as parse_openrocket_csv()
    """
    # .ork is a ZIP containing rocket.ork (XML)
    with zipfile.ZipFile(filepath, 'r') as zf:
        # Find the XML file inside
        names = zf.namelist()
        xml_name = None
        for n in names:
            if n.endswith('.ork'):
                xml_name = n
                break
        if xml_name is None:
            # Some .ork files have the XML at the top level
            xml_name = names[0]

        xml_data = zf.read(xml_name).decode('utf-8')

    root = ET.fromstring(xml_data)

    # Find all simulations
    sims = root.findall('.//simulation')
    if not sims:
        raise ValueError("No simulations found in .ork file. Run the sim in OpenRocket first.")

    if sim_index == -1:
        # List mode
        print(f"\nFound {len(sims)} simulation(s) in {filepath}:")
        for i, sim in enumerate(sims):
            name = sim.findtext('name', f'Sim {i}')
            fd = sim.find('.//flightdata')
            status = sim.get('status', 'unknown')
            if fd is not None:
                apogee = fd.get('maxaltitude', '?')
                max_v = fd.get('maxvelocity', '?')
                print(f"  [{i}] \"{name}\" (status={status}) — apogee={apogee}m, max_v={max_v}m/s")
            else:
                print(f"  [{i}] \"{name}\" (status={status}) — no flight data")
        return None, None, None

    if sim_index >= len(sims):
        raise ValueError(f"Sim index {sim_index} out of range (file has {len(sims)} sims)")

    sim = sims[sim_index]
    sim_name = sim.findtext('name', f'Sim {sim_index}')

    fd = sim.find('.//flightdata')
    if fd is None:
        raise ValueError(f"Simulation \"{sim_name}\" has no flight data. Run it in OpenRocket first.")

    # Parse the column types from the databranch
    db = fd.find('databranch')
    if db is None:
        raise ValueError(f"No databranch in simulation \"{sim_name}\"")

    types_str = db.get('types', '')
    col_names = [t.strip() for t in types_str.split(',')]

    # Map OpenRocket XML column names → our field names
    # These are the raw names from the types attribute (no units, lowercase-ish)
    ork_xml_map = {
        "Time": "time_s",
        "Altitude": "altitude_m",
        "Altitude above sea level": "altitude_asl_m",
        "Vertical velocity": "velocity_ms",
        "Total velocity": "total_velocity_ms",
        "Vertical acceleration": "acceleration_ms2",
        "Total acceleration": "total_acceleration_ms2",
        "Lateral velocity": "lateral_velocity_ms",
        "Lateral distance": "lateral_distance_m",
        "Lateral direction": "lateral_direction_deg",
        "Position East of launch": "pos_east_m",
        "Position North of launch": "pos_north_m",
        "Angle of attack": "aoa_deg",
        "Mass": "mass_kg",
        "Motor mass": "motor_mass_kg",
        "Thrust": "thrust_N",
        "Drag force": "drag_N",
        "Drag coefficient": "cd",
        "Normal force coefficient": "cn",
        "Stability margin calibers": "stability_cal",
        "CP location": "cp_m",
        "CG location": "cg_m",
        "Mach number": "mach",
        "Air pressure": "pressure_pa",
        "Air temperature": "temperature_c",
        "Air density": "air_density",
        "Speed of sound": "speed_of_sound",
        "Wind velocity": "wind_speed_ms",
        "Wind direction": "wind_direction_deg",
        "Gravitational acceleration": "gravity_ms2",
        "Roll rate": "roll_rate",
        "Pitch rate": "pitch_rate",
        "Yaw rate": "yaw_rate",
        "Vertical orientation (zenith)": "zenith_rad",
        "Lateral orientation (azimuth)": "azimuth_rad",
        "Thrust-to-weight ratio": "twr",
        "Reynolds number": "reynolds",
        "Simulation time step": "dt",
        "Reference length": "ref_length_m",
        "Reference area": "ref_area_m2",
    }

    field_indices = {}  # index → our_field_name
    for i, col in enumerate(col_names):
        mapped = ork_xml_map.get(col)
        if mapped:
            field_indices[i] = mapped

    # Parse events
    events = []
    event_type_map = {
        "launch": "LAUNCH",
        "ignition": "IGNITION",
        "liftoff": "LIFTOFF",
        "launchrod": "LAUNCHROD",
        "burnout": "BURNOUT",
        "apogee": "APOGEE",
        "ejectioncharge": "EJECTION_CHARGE",
        "recoverydevicedeployment": "RECOVERY_DEVICE_DEPLOYMENT",
        "groundhit": "GROUND_HIT",
        "simulationend": "SIMULATION_END",
        "tumble": "TUMBLE",
    }
    for ev_elem in fd.findall('.//event'):
        ev_type = event_type_map.get(ev_elem.get('type', ''), ev_elem.get('type', '').upper())
        ev_time = float(ev_elem.get('time', 0))
        events.append({"event": ev_type, "time_s": ev_time})

    # Parse datapoints
    rows = []
    for dp in db.findall('datapoint'):
        values = dp.text.strip().split(',')
        row = {}
        for i, field in field_indices.items():
            if i < len(values):
                val = values[i].strip()
                if val == 'NaN' or val == '':
                    row[field] = None
                else:
                    try:
                        row[field] = float(val)
                    except ValueError:
                        row[field] = None
        rows.append(row)

    # Air temperature in the XML is in Kelvin — convert to Celsius
    for row in rows:
        if row.get("temperature_c") is not None:
            row["temperature_c"] -= 273.15

    # Air pressure in the XML is in Pa — already correct

    # Assign flight states
    rows = assign_states(rows, events)

    # Summary from flightdata attributes
    metadata = {
        "source": str(filepath),
        "sim_name": sim_name,
        "sim_index": sim_index,
        "columns_found": list(set(field_indices.values())),
        "columns_raw": col_names,
        "n_events": len(events),
        "n_rows": len(rows),
        "max_altitude_m": float(fd.get('maxaltitude', 0)),
        "max_velocity_ms": float(fd.get('maxvelocity', 0)),
        "max_acceleration_ms2": float(fd.get('maxacceleration', 0)),
        "max_mach": float(fd.get('maxmach', 0)),
        "time_to_apogee_s": float(fd.get('timetoapogee', 0)),
        "flight_time_s": float(fd.get('flighttime', 0)),
        "ground_hit_velocity_ms": float(fd.get('groundhitvelocity', 0)),
        "launch_rod_velocity_ms": float(fd.get('launchrodvelocity', 0)),
    }

    return rows, events, metadata


def main():
    parser = argparse.ArgumentParser(
        description="Import OpenRocket simulation data for flight review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s rocket.ork                          → extract sim from .ork directly
  %(prog)s rocket.ork --sim 2                  → extract 3rd simulation
  %(prog)s rocket.ork --list-sims              → list all sims in .ork file
  %(prog)s sim_export.csv                      → sim_predicted.csv
  %(prog)s sim_export.csv -o my_sim.csv        → my_sim.csv
  %(prog)s sim_export.csv --json               → also outputs .json
  %(prog)s sim_export.csv --extract-params     → print rocket params
  %(prog)s --eng-info Cesaroni_H100.eng        → print motor stats
        """
    )
    parser.add_argument("infile", nargs="?", help="OpenRocket .ork file or CSV export")
    parser.add_argument("-o", "--output", default="sim_predicted.csv",
                       help="Output CSV path (default: sim_predicted.csv)")
    parser.add_argument("--sim", type=int, default=0,
                       help="Simulation index to extract from .ork file (default: 0)")
    parser.add_argument("--list-sims", action="store_true",
                       help="List all simulations in .ork file")
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

    if not args.infile:
        parser.error("Provide an OpenRocket .ork or CSV file (or use --eng-info)")

    is_ork = args.infile.lower().endswith('.ork')

    # List sims mode
    if args.list_sims:
        if not is_ork:
            parser.error("--list-sims only works with .ork files")
        parse_ork_file(args.infile, sim_index=-1)
        return

    # Parse input
    if is_ork:
        print(f"Parsing .ork: {args.infile} (sim index {args.sim})")
        rows, events, meta = parse_ork_file(args.infile, sim_index=args.sim)
    else:
        print(f"Parsing CSV: {args.infile}")
        rows, events, meta = parse_openrocket_csv(args.infile)

    print(f"  Sim: {meta.get('sim_name', 'N/A')}")
    print(f"  Rows: {meta['n_rows']}")
    print(f"  Columns: {', '.join(meta['columns_found'])}")
    print(f"  Events: {meta['n_events']}")

    # Print .ork summary stats if available
    if is_ork:
        print(f"\n  Flight summary:")
        print(f"    Apogee:           {meta['max_altitude_m']:.1f} m ({meta['max_altitude_m'] * 3.281:.0f} ft)")
        print(f"    Max velocity:     {meta['max_velocity_ms']:.1f} m/s (Mach {meta['max_mach']:.3f})")
        print(f"    Max acceleration: {meta['max_acceleration_ms2']:.1f} m/s² ({meta['max_acceleration_ms2'] / 9.81:.1f} G)")
        print(f"    Time to apogee:   {meta['time_to_apogee_s']:.2f} s")
        print(f"    Flight time:      {meta['flight_time_s']:.1f} s")
        print(f"    Landing velocity: {meta['ground_hit_velocity_ms']:.1f} m/s")
        print(f"    Rod departure:    {meta['launch_rod_velocity_ms']:.1f} m/s")

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
