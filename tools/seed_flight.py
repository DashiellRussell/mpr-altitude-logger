#!/usr/bin/env python3
"""
Generate a synthetic binary flight log for testing the post-flight pipeline.

Uses the simulator physics to produce a realistic flight profile, adds sensor
noise, then writes it out in the same binary format as the on-board logger.

Usage:
    python tools/seed_flight.py                          # defaults: H100 motor, 2.5kg
    python tools/seed_flight.py -o test_flight.bin       # custom output
    python tools/seed_flight.py --motor Estes_E12 --mass 0.8 --diameter 0.041
    python tools/seed_flight.py --verify                 # generate + decode round-trip
"""

import os
import struct
import math
import random
import argparse
from pathlib import Path

# Add parent tools dir so we can import the simulator
import sys
sys.path.insert(0, str(Path(__file__).parent))
from simulate import simulate, MOTORS, pressure_at_altitude


# ── Binary format (must match logging/datalog.py) ──────────────

FRAME_HEADER = b'\xAA\x55'
FRAME_FORMAT = '<IB f f f f f HHH B'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)

STATE_MAP = {"PAD": 0, "BOOST": 1, "COAST": 2, "APOGEE": 3,
             "DROGUE": 4, "MAIN": 5, "LANDED": 6}


def add_sensor_noise(value, std_dev):
    """Add Gaussian noise to a value."""
    return value + random.gauss(0, std_dev)


def simple_kalman_filter(raw_values, q_alt=0.1, q_vel=0.5, r_alt=1.0, dt=0.04):
    """
    Run a 1D constant-velocity Kalman filter over raw altitude readings.
    Mimics the on-board filter so the seed data looks like real logged output.
    """
    # State: [altitude, velocity]
    x_alt = raw_values[0] if raw_values else 0.0
    x_vel = 0.0
    p00, p01, p10, p11 = 1.0, 0.0, 0.0, 1.0

    filtered = []
    for z in raw_values:
        # Predict
        x_alt_pred = x_alt + x_vel * dt
        x_vel_pred = x_vel
        p00_pred = p00 + dt * (p10 + p01) + dt * dt * p11 + q_alt
        p01_pred = p01 + dt * p11
        p10_pred = p10 + dt * p11
        p11_pred = p11 + q_vel

        # Update
        y = z - x_alt_pred
        s = p00_pred + r_alt
        k0 = p00_pred / s
        k1 = p10_pred / s

        x_alt = x_alt_pred + k0 * y
        x_vel = x_vel_pred + k1 * y
        p00 = (1 - k0) * p00_pred
        p01 = (1 - k0) * p01_pred
        p10 = -k1 * p00_pred + p10_pred
        p11 = -k1 * p01_pred + p11_pred

        filtered.append((x_alt, x_vel))

    return filtered


def generate_flight_log(motor="Cesaroni_H100", mass=2.5, cd=0.45, diameter=0.054,
                        main_deploy_alt=300.0, pad_seconds=3.0, landed_seconds=5.0,
                        noise_alt_std=0.5, noise_pressure_std=10.0,
                        noise_temp_std=0.1, seed=42):
    """
    Run a sim and produce binary frame data.

    Returns list of (timestamp_ms, state, pressure, temp, alt_raw,
                     alt_filtered, vel_filtered, v3v3, v5v, v9v, flags)
    """
    random.seed(seed)

    # First pass: find apogee to auto-scale main deploy alt if needed
    sim_preview = simulate(
        rocket_mass_kg=mass,
        motor_name_or_curve=motor,
        cd=cd,
        diameter_m=diameter,
        main_deploy_alt=main_deploy_alt,
    )
    if not sim_preview:
        raise RuntimeError("Simulation produced no results")

    peak_alt = max(r["altitude_m"] for r in sim_preview)
    # If main deploy alt is above apogee, set it to 40% of apogee so we
    # actually see APOGEE → DROGUE → MAIN transitions in the data
    effective_main_alt = min(main_deploy_alt, peak_alt * 0.4)

    sim_results = simulate(
        rocket_mass_kg=mass,
        motor_name_or_curve=motor,
        cd=cd,
        diameter_m=diameter,
        main_deploy_alt=effective_main_alt,
    )

    if not sim_results:
        raise RuntimeError("Simulation produced no results")

    dt = 0.04  # 25 Hz
    sample_rate = 25

    # Ground reference altitude (launch site ~200m ASL)
    ground_alt_asl = 200.0
    ground_pressure = pressure_at_altitude(ground_alt_asl)
    ground_temp = 288.15 - 0.0065 * ground_alt_asl  # ISA temp at site

    frames = []

    # Pad phase — sitting on the pad before launch
    pad_samples = int(pad_seconds * sample_rate)
    for i in range(pad_samples):
        t_ms = int(i * 1000 / sample_rate)
        pressure = add_sensor_noise(ground_pressure, noise_pressure_std)
        temp_c = add_sensor_noise(ground_temp - 273.15, noise_temp_std)
        alt_raw = add_sensor_noise(0.0, noise_alt_std)
        frames.append({
            "t_ms": t_ms,
            "state": "PAD",
            "pressure": pressure,
            "temp_c": temp_c,
            "alt_raw": alt_raw,
        })

    # Flight phase — from sim results
    # The simulator lumps APOGEE into a single instant. We inject a proper
    # APOGEE state for a few frames at the coast→drogue transition to match
    # what the real state machine would produce.
    t_offset_ms = int(pad_seconds * 1000)
    prev_state = "PAD"

    for r in sim_results:
        t_ms = t_offset_ms + int(r["time_s"] * 1000)
        alt_agl = r["altitude_m"]
        pressure = add_sensor_noise(r["pressure_pa"], noise_pressure_std)
        temp_k = 288.15 - 0.0065 * min(ground_alt_asl + alt_agl, 11000)
        temp_c = add_sensor_noise(temp_k - 273.15, noise_temp_std)
        alt_raw = add_sensor_noise(alt_agl, noise_alt_std)

        state = r["state"]
        # Inject APOGEE state at the coast→drogue boundary
        if prev_state == "COAST" and state in ("APOGEE", "DROGUE"):
            state = "APOGEE"
        elif prev_state == "APOGEE":
            state = "DROGUE"

        prev_state = state

        frames.append({
            "t_ms": t_ms,
            "state": state,
            "pressure": pressure,
            "temp_c": temp_c,
            "alt_raw": alt_raw,
        })

    # Landed phase — sitting on the ground after touchdown
    last_t = frames[-1]["t_ms"]
    landed_samples = int(landed_seconds * sample_rate)
    for i in range(landed_samples):
        t_ms = last_t + int((i + 1) * 1000 / sample_rate)
        pressure = add_sensor_noise(ground_pressure, noise_pressure_std)
        temp_c = add_sensor_noise(ground_temp - 273.15, noise_temp_std)
        alt_raw = add_sensor_noise(0.0, noise_alt_std)
        frames.append({
            "t_ms": t_ms,
            "state": "LANDED",
            "pressure": pressure,
            "temp_c": temp_c,
            "alt_raw": alt_raw,
        })

    # Run Kalman filter over raw altitudes
    raw_alts = [f["alt_raw"] for f in frames]
    filtered = simple_kalman_filter(raw_alts, dt=1.0 / sample_rate)

    # Build binary frames
    binary_frames = []
    for i, f in enumerate(frames):
        alt_filt, vel_filt = filtered[i]

        # Simulate voltage rails with slight droop under load during boost
        state_val = STATE_MAP[f["state"]]
        boost_droop = 50 if f["state"] == "BOOST" else 0
        v_3v3 = int(add_sensor_noise(3300 - boost_droop * 0.1, 5))
        v_5v = int(add_sensor_noise(5000 - boost_droop * 0.3, 8))
        v_9v = int(add_sensor_noise(9000 - boost_droop, 15))

        flags = 0

        binary_frames.append((
            f["t_ms"],
            state_val,
            f["pressure"],
            f["temp_c"],
            f["alt_raw"],
            alt_filt,
            vel_filt,
            v_3v3,
            v_5v,
            v_9v,
            flags,
        ))

    return binary_frames


def write_binary_log(frames, output_path):
    """Write frames to binary log file matching the on-board format."""
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    with open(output_path, 'wb') as f:
        # File header
        f.write(b'RKTLOG')
        f.write(struct.pack('<HH', 2, FRAME_SIZE))

        for frame in frames:
            f.write(FRAME_HEADER)
            f.write(struct.pack(FRAME_FORMAT, *frame))

    n_bytes = 10 + len(frames) * (2 + FRAME_SIZE)
    print(f"Wrote {len(frames)} frames ({n_bytes} bytes) to {output_path}")


def write_preflight_txt(flight_dir, motor, mass, cd, diameter, n_frames):
    """Write a preflight.txt metadata file to match the on-board format."""
    lines = [
        'UNSW Rocketry — MPR Altitude Logger',
        'Avionics v1.4.1 (SIMULATED)',
        f'Seed flight generator',
        '',
        '--- Preflight Results ---',
        'Manual override: NO',
        'Errors: None',
        'Ground pressure: 98717 Pa',
        'Voltages: 3V3=3300mV 5V=5000mV 9V=9000mV',
        f'Barometer: 98717 Pa, 14.8 C',
        'Sample rate: 25 Hz',
        f'Log file: {flight_dir}/flight.bin',
        '',
        '--- Simulation Parameters ---',
        f'Motor: {motor}',
        f'Mass: {mass} kg',
        f'Cd: {cd}',
        f'Diameter: {diameter} m',
        f'Frames: {n_frames}',
    ]
    preflight_path = os.path.join(flight_dir, 'preflight.txt')
    with open(preflight_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"Wrote preflight metadata to {preflight_path}")


def find_sd_card():
    """Auto-detect mounted SD card on macOS (/Volumes/)."""
    import os
    system_vols = {
        'Macintosh HD', 'Macintosh HD - Data', 'Recovery', 'Preboot',
        'VM', 'Update', 'com.apple.TimeMachine.localsnapshots',
    }
    volumes_dir = '/Volumes'
    if not os.path.isdir(volumes_dir):
        return None

    for vol in os.listdir(volumes_dir):
        if vol in system_vols or vol.startswith('.'):
            continue
        vol_path = os.path.join(volumes_dir, vol)
        if os.path.isdir(vol_path):
            return vol_path
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic flight log for testing post-flight tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                        → seed_flight.bin in current dir
  %(prog)s --sd                   → write directly to mounted SD card
  %(prog)s --sd --motor Estes_E12 → smaller motor, write to SD
  %(prog)s --verify               → generate + decode round-trip test
        """)
    parser.add_argument("-o", "--output", default="seed_flight.bin",
                        help="Output path (default: seed_flight/flight.bin)")
    parser.add_argument("--sd", action="store_true",
                        help="Auto-detect SD card and write flight log there")
    parser.add_argument("--motor", default="Cesaroni_H100",
                        help=f"Motor: {list(MOTORS.keys())} (default: Cesaroni_H100)")
    parser.add_argument("--mass", type=float, default=2.5,
                        help="Dry mass in kg (default: 2.5)")
    parser.add_argument("--cd", type=float, default=0.45,
                        help="Drag coefficient (default: 0.45)")
    parser.add_argument("--diameter", type=float, default=0.054,
                        help="Body diameter in meters (default: 0.054)")
    parser.add_argument("--main-alt", type=float, default=300.0,
                        help="Main deploy altitude AGL in meters (default: 300)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducible noise (default: 42)")
    parser.add_argument("--verify", action="store_true",
                        help="Run decode_log.py on the output to verify round-trip")
    args = parser.parse_args()

    # Determine output path
    output_path = args.output
    flight_dir = None

    if args.sd:
        sd_path = find_sd_card()
        if sd_path is None:
            print("Error: No SD card found. Insert an SD card and try again.")
            print("Checked /Volumes/ for non-system volumes.")
            sys.exit(1)
        print(f"SD card found: {sd_path}")

        # Find next available flight folder (match on-board naming)
        idx = 1
        while True:
            d = os.path.join(sd_path, f'flight_{idx:03d}')
            if not os.path.isdir(d):
                break
            idx += 1
        flight_dir = d
        output_path = os.path.join(flight_dir, 'flight.bin')
    elif output_path == 'seed_flight.bin':
        # Default: use per-flight folder layout in current dir
        flight_dir = 'seed_flight'
        output_path = os.path.join(flight_dir, 'flight.bin')

    print(f"Generating flight: motor={args.motor}, mass={args.mass}kg, Cd={args.cd}")

    frames = generate_flight_log(
        motor=args.motor,
        mass=args.mass,
        cd=args.cd,
        diameter=args.diameter,
        main_deploy_alt=args.main_alt,
        seed=args.seed,
    )

    write_binary_log(frames, output_path)

    # Write preflight.txt metadata alongside the bin
    if flight_dir:
        write_preflight_txt(
            flight_dir, args.motor, args.mass, args.cd, args.diameter, len(frames),
        )

    if args.sd:
        print(f"\nSD card ready! Eject and run:")
        print(f"  pnpm --filter @mpr/tui dev -- postflight")

    if args.verify:
        print("\n── Verification: decoding generated log ──")
        from decode_log import decode_file, print_summary
        decoded = decode_file(output_path)
        print_summary(decoded)


if __name__ == "__main__":
    main()
