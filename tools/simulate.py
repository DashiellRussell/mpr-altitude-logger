#!/usr/bin/env python3
"""
1D rocket flight simulator — generates predicted altitude/velocity profiles.

Uses simple Euler integration with:
    - Motor thrust curve (from .eng file or manual input)
    - Aerodynamic drag (Cd * A * 0.5 * rho * v^2)
    - Gravity
    - Barometric atmosphere model (density vs altitude)

Outputs a CSV that the review dashboard can overlay against actual flight data.

Usage:
    python simulate.py --mass 2.5 --motor Cesaroni_H100 --cd 0.45 --diameter 0.054
    python simulate.py --config rocket.json
    python simulate.py --motor-file H100.eng --mass 2.5 --cd 0.5 --diameter 0.054
"""

import json
import csv
import math
import argparse
from pathlib import Path


# ── Atmosphere Model ──────────────────────────────────────────

def air_density(altitude_m):
    """ISA atmosphere model — density (kg/m³) as function of altitude."""
    T0 = 288.15      # sea level temp (K)
    P0 = 101325.0     # sea level pressure (Pa)
    L = 0.0065        # lapse rate (K/m)
    R = 287.058        # gas constant for air
    g = 9.80665
    M = 0.0289644      # molar mass of air

    if altitude_m < 0:
        altitude_m = 0
    if altitude_m > 11000:
        altitude_m = 11000  # troposphere only

    T = T0 - L * altitude_m
    P = P0 * (T / T0) ** (g / (L * R))
    rho = P / (R * T)
    return rho


def pressure_at_altitude(altitude_m):
    """ISA pressure (Pa) at given altitude — for simulating barometer readings."""
    T0 = 288.15
    P0 = 101325.0
    L = 0.0065
    R = 287.058
    g = 9.80665

    if altitude_m < 0:
        altitude_m = 0

    T = T0 - L * altitude_m
    P = P0 * (T / T0) ** (g / (L * R))
    return P


# ── Motor Models ──────────────────────────────────────────────

# Built-in motor database (thrust in N, total impulse in Ns, burn time in s)
MOTORS = {
    # Format: {"thrust_avg": N, "thrust_peak": N, "total_impulse": Ns,
    #          "burn_time": s, "prop_mass": kg, "total_mass": kg}
    "Estes_D12": {
        "thrust_avg": 11.8, "thrust_peak": 29.7, "total_impulse": 16.84,
        "burn_time": 1.6, "prop_mass": 0.0214, "total_mass": 0.044,
    },
    "Estes_E12": {
        "thrust_avg": 11.48, "thrust_peak": 22.4, "total_impulse": 28.47,
        "burn_time": 2.48, "prop_mass": 0.038, "total_mass": 0.057,
    },
    "Cesaroni_F32": {
        "thrust_avg": 32.0, "thrust_peak": 41.0, "total_impulse": 37.8,
        "burn_time": 1.18, "prop_mass": 0.021, "total_mass": 0.061,
    },
    "Cesaroni_G40": {
        "thrust_avg": 40.0, "thrust_peak": 55.0, "total_impulse": 77.0,
        "burn_time": 1.9, "prop_mass": 0.039, "total_mass": 0.093,
    },
    "Cesaroni_H100": {
        "thrust_avg": 100.0, "thrust_peak": 130.0, "total_impulse": 176.0,
        "burn_time": 1.76, "prop_mass": 0.084, "total_mass": 0.176,
    },
    "Cesaroni_I218": {
        "thrust_avg": 218.0, "thrust_peak": 274.0, "total_impulse": 365.0,
        "burn_time": 1.67, "prop_mass": 0.163, "total_mass": 0.310,
    },
    "Aerotech_J350": {
        "thrust_avg": 350.0, "thrust_peak": 410.0, "total_impulse": 650.0,
        "burn_time": 1.86, "prop_mass": 0.312, "total_mass": 0.562,
    },
}


def parse_eng_file(filepath):
    """
    Parse RASP .eng motor file format.
    Returns list of (time_s, thrust_N) tuples.
    """
    points = []
    header_read = False

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            parts = line.split()
            if not header_read:
                # Header line: name diameter length delays prop_mass total_mass manufacturer
                header_read = True
                continue
            if len(parts) >= 2:
                t = float(parts[0])
                thrust = float(parts[1])
                points.append((t, thrust))

    return points


def thrust_curve_from_motor(motor_data):
    """
    Generate simplified thrust curve from motor database entry.
    Uses a trapezoidal profile: ramp up → peak → average → tail off.
    """
    burn = motor_data["burn_time"]
    peak = motor_data["thrust_peak"]
    avg = motor_data["thrust_avg"]

    # Simple profile: 10% ramp, 20% peak, 50% sustain, 20% tail
    points = [
        (0.0, 0.0),
        (burn * 0.05, peak),
        (burn * 0.15, peak * 0.95),
        (burn * 0.30, avg * 1.1),
        (burn * 0.80, avg * 0.95),
        (burn * 0.95, avg * 0.3),
        (burn, 0.0),
    ]
    return points


def interpolate_thrust(curve, t):
    """Linear interpolation of thrust curve at time t."""
    if t <= curve[0][0]:
        return curve[0][1]
    if t >= curve[-1][0]:
        return 0.0

    for i in range(len(curve) - 1):
        t0, f0 = curve[i]
        t1, f1 = curve[i + 1]
        if t0 <= t <= t1:
            frac = (t - t0) / (t1 - t0) if (t1 - t0) > 0 else 0
            return f0 + frac * (f1 - f0)
    return 0.0


# ── Simulator ─────────────────────────────────────────────────

def simulate(rocket_mass_kg, motor_name_or_curve, cd, diameter_m,
             dt=0.001, max_time=300.0, rail_length_m=1.5,
             drogue_cd=1.5, drogue_diameter_m=0.3,
             main_cd=2.0, main_diameter_m=0.8,
             main_deploy_alt=300.0):
    """
    Run 1D flight simulation.

    Args:
        rocket_mass_kg: dry mass (without motor propellant)
        motor_name_or_curve: motor name string or list of (t, thrust_N) tuples
        cd: drag coefficient (body)
        diameter_m: rocket body diameter (m)
        dt: timestep (s)
        max_time: max sim duration (s)
        rail_length_m: launch rail length
        drogue_*: drogue chute parameters
        main_*: main chute parameters
        main_deploy_alt: main deployment altitude AGL (m)

    Returns:
        list of dicts with t, alt, vel, accel, mach, thrust, drag, state, pressure
    """
    g = 9.80665
    area = math.pi * (diameter_m / 2) ** 2
    drogue_area = math.pi * (drogue_diameter_m / 2) ** 2
    main_area = math.pi * (main_diameter_m / 2) ** 2

    # Get thrust curve
    if isinstance(motor_name_or_curve, str):
        if motor_name_or_curve in MOTORS:
            motor = MOTORS[motor_name_or_curve]
            curve = thrust_curve_from_motor(motor)
            prop_mass = motor["prop_mass"]
            burn_time = motor["burn_time"]
        else:
            raise ValueError(f"Unknown motor: {motor_name_or_curve}. "
                           f"Available: {list(MOTORS.keys())}")
    else:
        curve = motor_name_or_curve
        burn_time = curve[-1][0]
        # Estimate prop mass from total impulse
        total_impulse = sum(
            (curve[i][1] + curve[i+1][1]) / 2 * (curve[i+1][0] - curve[i][0])
            for i in range(len(curve) - 1)
        )
        prop_mass = total_impulse / (2500 * g)  # rough estimate

    # State
    alt = 0.0
    vel = 0.0
    t = 0.0
    off_rail = False
    apogee_reached = False
    drogue_deployed = False
    main_deployed = False
    max_alt = 0.0
    landed = False

    results = []

    while t < max_time and not landed:
        # Current mass (linear propellant burn)
        if t < burn_time:
            frac_burned = t / burn_time
            current_mass = rocket_mass_kg + prop_mass * (1 - frac_burned)
        else:
            current_mass = rocket_mass_kg

        # Thrust
        thrust = interpolate_thrust(curve, t)

        # Atmosphere
        rho = air_density(alt)
        pressure = pressure_at_altitude(alt)

        # Speed of sound (approximate)
        T = 288.15 - 0.0065 * min(alt, 11000)
        speed_of_sound = math.sqrt(1.4 * 287.058 * T)
        mach = abs(vel) / speed_of_sound if speed_of_sound > 0 else 0

        # Drag
        if drogue_deployed and not main_deployed:
            effective_cd = drogue_cd
            effective_area = drogue_area
        elif main_deployed:
            effective_cd = main_cd
            effective_area = main_area
        else:
            effective_cd = cd
            effective_area = area

        drag = 0.5 * rho * vel * abs(vel) * effective_cd * effective_area

        # Net force
        net_force = thrust - drag - current_mass * g

        # On rail: no lateral motion, just along rail axis
        if not off_rail:
            if alt >= rail_length_m:
                off_rail = True
            elif net_force < 0:
                net_force = 0  # can't go backwards on rail

        accel = net_force / current_mass

        # Integration (Euler)
        vel += accel * dt
        alt += vel * dt

        # Ground clamp
        if alt < 0:
            alt = 0
            vel = 0
            if apogee_reached:
                landed = True

        # Track max altitude
        if alt > max_alt:
            max_alt = alt

        # State detection
        state = "PAD"
        if thrust > 0 and off_rail:
            state = "BOOST"
        elif thrust <= 0 and vel > 0 and not apogee_reached:
            state = "COAST"
        elif vel <= 0 and not apogee_reached and max_alt > 5:
            apogee_reached = True
            drogue_deployed = True
            state = "APOGEE"
        elif drogue_deployed and not main_deployed:
            state = "DROGUE"
            if alt <= main_deploy_alt:
                main_deployed = True
                state = "MAIN"
        elif main_deployed:
            state = "MAIN"
        if landed:
            state = "LANDED"

        # Record at ~25 Hz to match flight logger
        if int(t / 0.04) != int((t - dt) / 0.04) or t < dt * 2:
            results.append({
                "time_s": round(t, 4),
                "altitude_m": round(alt, 3),
                "velocity_ms": round(vel, 3),
                "acceleration_ms2": round(accel, 2),
                "mach": round(mach, 4),
                "thrust_N": round(thrust, 2),
                "drag_N": round(drag, 2),
                "mass_kg": round(current_mass, 4),
                "pressure_pa": round(pressure, 1),
                "air_density": round(rho, 4),
                "state": state,
            })

        t += dt

    return results


def save_csv(results, filepath):
    """Save simulation results to CSV."""
    if not results:
        return
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"Saved {len(results)} points to {filepath}")


def save_json(results, filepath):
    """Save as JSON for the review dashboard."""
    with open(filepath, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Saved JSON to {filepath}")


def print_summary(results):
    """Print key simulation metrics."""
    if not results:
        print("No results!")
        return

    max_alt = max(r["altitude_m"] for r in results)
    max_vel = max(r["velocity_ms"] for r in results)
    max_mach = max(r["mach"] for r in results)
    max_accel = max(r["acceleration_ms2"] for r in results)

    # Find apogee time
    apogee_t = 0
    for r in results:
        if r["altitude_m"] == max_alt:
            apogee_t = r["time_s"]
            break

    # Find burnout
    burnout_t = 0
    for r in results:
        if r["thrust_N"] > 0:
            burnout_t = r["time_s"]

    # Total flight time
    total_t = results[-1]["time_s"]

    print("\n╔══════════════════════════════════════════╗")
    print("║        SIMULATION RESULTS                ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  Apogee:        {max_alt:8.1f} m AGL           ║")
    print(f"║  Max velocity:  {max_vel:8.1f} m/s             ║")
    print(f"║  Max Mach:      {max_mach:8.4f}                ║")
    print(f"║  Max accel:     {max_accel:8.1f} m/s² ({max_accel/9.81:.1f} G)    ║")
    print(f"║  Burnout:       T+{burnout_t:.2f} s                ║")
    print(f"║  Apogee time:   T+{apogee_t:.2f} s                ║")
    print(f"║  Total flight:  {total_t:.1f} s                  ║")
    print("╚══════════════════════════════════════════╝\n")


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="1D Rocket Flight Simulator")
    parser.add_argument("--mass", type=float, required=True,
                       help="Dry mass in kg (without motor propellant)")
    parser.add_argument("--motor", type=str, default=None,
                       help=f"Motor name from database: {list(MOTORS.keys())}")
    parser.add_argument("--motor-file", type=str, default=None,
                       help="Path to RASP .eng thrust curve file")
    parser.add_argument("--cd", type=float, default=0.45,
                       help="Drag coefficient (default 0.45)")
    parser.add_argument("--diameter", type=float, required=True,
                       help="Rocket body diameter in meters")
    parser.add_argument("--rail", type=float, default=1.5,
                       help="Launch rail length in meters (default 1.5)")
    parser.add_argument("--main-alt", type=float, default=300,
                       help="Main deploy altitude AGL in meters (default 300)")
    parser.add_argument("-o", "--output", type=str, default="sim_predicted.csv",
                       help="Output CSV filename")
    parser.add_argument("--json", action="store_true",
                       help="Also output JSON for dashboard")

    args = parser.parse_args()

    if args.motor_file:
        curve = parse_eng_file(args.motor_file)
        motor = curve
    elif args.motor:
        motor = args.motor
    else:
        parser.error("Specify either --motor or --motor-file")

    results = simulate(
        rocket_mass_kg=args.mass,
        motor_name_or_curve=motor,
        cd=args.cd,
        diameter_m=args.diameter,
        rail_length_m=args.rail,
        main_deploy_alt=args.main_alt,
    )

    print_summary(results)
    save_csv(results, args.output)

    if args.json:
        json_path = Path(args.output).with_suffix('.json')
        save_json(results, json_path)


if __name__ == "__main__":
    main()
