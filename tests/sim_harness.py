"""
Simulated Pico environment — runs the full sensor→filter→FSM→logger pipeline
on a laptop with synthetic data, no hardware required.

Feeds pressure/temperature through pressure_to_altitude → Kalman → state machine → logger,
mirroring main.py's Core 0 loop without any hardware dependencies.
"""

import os
import sys
import math
import random
import struct
import tempfile
import importlib.util

# ── Import simulate.py via importlib (avoid tools/ not being a package) ──
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_sim_spec = importlib.util.spec_from_file_location(
    'simulate',
    os.path.join(_project_root, 'tools', 'simulate.py'),
)
_sim_mod = importlib.util.module_from_spec(_sim_spec)
_sim_spec.loader.exec_module(_sim_mod)
simulate = _sim_mod.simulate

# ── Import datalog via importlib (logging/ shadows stdlib) ──
_dl_spec = importlib.util.spec_from_file_location(
    'proj_datalog',
    os.path.join(_project_root, 'logging', 'datalog.py'),
)
_dl_mod = importlib.util.module_from_spec(_dl_spec)
_dl_spec.loader.exec_module(_dl_mod)
FlightLogger = _dl_mod.FlightLogger
next_flight_dir = _dl_mod.next_flight_dir

# ── Import decode_log via importlib ──
_dec_spec = importlib.util.spec_from_file_location(
    'decode_log',
    os.path.join(_project_root, 'tools', 'decode_log.py'),
)
_dec_mod = importlib.util.module_from_spec(_dec_spec)
_dec_spec.loader.exec_module(_dec_mod)
decode_file = _dec_mod.decode_file

# ── Normal imports (conftest mocks handle MicroPython) ──
from sensors.barometer import pressure_to_altitude
from flight.kalman import AltitudeKalman
from flight.state_machine import FlightStateMachine, PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED, STATE_NAMES


# Sentinel for sensor faults — None means "no reading available"
SENSOR_FAULT = None

# Global to capture the last SimResult for TUI display
_last_result = None


# ── Sensor Providers ─────────────────────────────────────────

def from_simulate(motor='Cesaroni_H100', mass=2.5, cd=0.45, diameter=0.054,
                  post_land_seconds=8, **kw):
    """Wrap simulate() output as a pressure/temperature generator.

    Uses simulate()'s pressure_pa output directly — no round-trip altitude conversion.
    Appends extra ground-level frames after landing so the FSM's LANDED
    confirmation window (5s of near-zero velocity) can be satisfied.
    """
    results = simulate(
        rocket_mass_kg=mass,
        motor_name_or_curve=motor,
        cd=cd,
        diameter_m=diameter,
        **kw,
    )
    ground_pressure = results[0]['pressure_pa'] if results else 101325.0
    for r in results:
        yield (r['pressure_pa'], 20.0)

    # Pad with ground-level frames so LANDED can confirm
    sample_rate = 25  # matches simulate's ~25 Hz output
    for _ in range(post_land_seconds * sample_rate):
        yield (ground_pressure, 20.0)


def from_pressure_sequence(pressures, temperature=20.0):
    """Yield (pressure, temperature) from a raw list of pressure values."""
    for p in pressures:
        yield (p, temperature)


def constant(pressure_pa=101325.0, temperature_c=20.0, n_frames=250):
    """Steady-state provider — constant pressure for pad/ground testing."""
    for _ in range(n_frames):
        yield (pressure_pa, temperature_c)


def noise_overlay(base, noise_std=50.0, temp_noise_std=0.5):
    """Wrap any provider, adding Gaussian noise to pressure and temperature."""
    for p, t in base:
        yield (p + random.gauss(0, noise_std), t + random.gauss(0, temp_noise_std))


# ── Fault Injectors ──────────────────────────────────────────

def sensor_dropout(base, at, duration=10):
    """Yield SENSOR_FAULT for `duration` frames starting at frame index `at`."""
    for i, val in enumerate(base):
        if at <= i < at + duration:
            yield SENSOR_FAULT
        else:
            yield val


def pressure_spike(base, at, value=50000.0, duration=1):
    """Replace pressure with a spike value for `duration` frames at index `at`."""
    for i, (p, t) in enumerate(base):
        if at <= i < at + duration:
            yield (value, t)
        else:
            yield (p, t)


def gradual_drift(base, start, rate_pa_per_frame=2.0):
    """Add linearly increasing pressure offset starting at frame `start`."""
    for i, (p, t) in enumerate(base):
        if i >= start:
            yield (p + (i - start) * rate_pa_per_frame, t)
        else:
            yield (p, t)


def stuck_sensor(base, at, duration=100):
    """Freeze output at the value it had at frame `at` for `duration` frames."""
    frozen = None
    for i, val in enumerate(base):
        if i == at:
            frozen = val
        if frozen is not None and at <= i < at + duration:
            yield frozen
        else:
            yield val


def intermittent_dropout(base, intervals):
    """Multiple sensor dropout windows.

    intervals: list of (start_frame, duration) tuples.
    """
    for i, val in enumerate(base):
        is_fault = False
        for start, dur in intervals:
            if start <= i < start + dur:
                is_fault = True
                break
        yield SENSOR_FAULT if is_fault else val


def angled_flight(base, effective_fraction=0.7):
    """Simulate off-axis flight by reducing altitude (pressure deviation from ground).

    If a rocket tips, the barometric altitude is genuinely lower.
    This reduces the pressure change from ground level by the given fraction.
    effective_fraction=0.7 means the rocket reaches ~70% of nominal altitude.
    """
    data = list(base)
    if not data:
        return
    # Find ground pressure from first valid reading
    ground_p = None
    for item in data:
        if item is not SENSOR_FAULT:
            ground_p = item[0]
            break
    if ground_p is None:
        yield from data
        return
    for item in data:
        if item is SENSOR_FAULT:
            yield item
        else:
            p, t = item
            delta = p - ground_p  # negative when above ground
            yield (ground_p + delta * effective_fraction, t)


def below_ground_landing(base, valley_depth_pa=600.0):
    """Simulate landing below launch altitude (e.g. launched from a hill).

    After the flight, pressure increases above ground level — negative AGL.
    valley_depth_pa ~= 600 Pa ≈ 50m below launch site.
    The transition is gradual over the descent phase.
    """
    data = list(base)
    if not data:
        return
    # Find the approximate landing region (last 30% of frames)
    n = len(data)
    descent_start = int(n * 0.5)
    for i, item in enumerate(data):
        if item is SENSOR_FAULT:
            yield item
        else:
            p, t = item
            if i > descent_start:
                # Gradually add pressure (simulates descending into valley)
                progress = (i - descent_start) / (n - descent_start)
                yield (p + valley_depth_pa * progress, t)
            else:
                yield (p, t)


def temperature_ramp(base, rate_c_per_frame=0.02):
    """Ramp temperature over time (thermal soak in sun, or altitude cooling)."""
    for i, item in enumerate(base):
        if item is SENSOR_FAULT:
            yield item
        else:
            p, t = item
            yield (p, t + i * rate_c_per_frame)


# ── SimResult ─────────────────────────────────────────────────

class SimResult:
    """Results from a PicoSim run."""

    def __init__(self, frames, transitions, fsm, bin_path=None):
        self.frames = frames
        self.transitions = transitions
        self.fsm = fsm
        self.bin_path = bin_path

    @property
    def states_visited(self):
        """Ordered unique states seen during the flight."""
        seen = []
        for f in self.frames:
            if not seen or seen[-1] != f['state']:
                seen.append(f['state'])
        return seen

    @property
    def max_altitude(self):
        return max((f['alt_filtered'] for f in self.frames), default=0.0)

    @property
    def max_velocity(self):
        return max((f['vel_filtered'] for f in self.frames), default=0.0)

    @property
    def flight_duration_s(self):
        if len(self.frames) < 2:
            return 0.0
        return (self.frames[-1]['timestamp_ms'] - self.frames[0]['timestamp_ms']) / 1000.0

    def state_at(self, time_s):
        """Return the state at a given time (seconds from start)."""
        target_ms = self.frames[0]['timestamp_ms'] + time_s * 1000
        for f in self.frames:
            if f['timestamp_ms'] >= target_ms:
                return f['state']
        return self.frames[-1]['state']

    def error_frames(self):
        """Return list of frames that have the error flag set."""
        return [f for f in self.frames if f.get('is_error', False)]

    def reached_state(self, state):
        """Check if a given state was ever visited."""
        return any(f['state'] == state for f in self.frames)


# ── PicoSim ──────────────────────────────────────────────────

class PicoSim:
    """
    Runs the full avionics pipeline with synthetic sensor data.

    Mirrors main.py lines 346-418: read sensor → altitude → Kalman → FSM → log.
    """

    def __init__(self, sensor_provider, sample_rate_hz=25,
                 ground_cal_frames=50, ground_pressure=None,
                 voltage_provider=None, write_bin=False, bin_dir=None):
        self.provider = sensor_provider
        self.sample_rate_hz = sample_rate_hz
        self.ground_cal_frames = ground_cal_frames
        self.ground_pressure_override = ground_pressure
        self.voltage_provider = voltage_provider
        self.write_bin = write_bin
        self.bin_dir = bin_dir

    def run(self):
        """Execute the full pipeline, return SimResult."""
        dt = 1.0 / self.sample_rate_hz
        dt_ms = 1000.0 / self.sample_rate_hz
        now_ms = 0.0

        kalman = AltitudeKalman()
        fsm = FlightStateMachine()

        frames = []
        transitions = []
        prev_state = PAD

        # Collect all provider output
        raw_data = list(self.provider)

        # Ground calibration
        if self.ground_pressure_override is not None:
            ground_pressure = self.ground_pressure_override
            cal_end = 0
        else:
            cal_end = min(self.ground_cal_frames, len(raw_data))
            pressure_sum = 0.0
            cal_count = 0
            for i in range(cal_end):
                if raw_data[i] is not SENSOR_FAULT:
                    pressure_sum += raw_data[i][0]
                    cal_count += 1
            ground_pressure = pressure_sum / cal_count if cal_count > 0 else 101325.0

        # Initialize filter and FSM
        kalman.reset(0.0)
        fsm.set_ground_reference(0.0)

        # Voltage provider iterator
        volt_iter = iter(self.voltage_provider) if self.voltage_provider else None

        # Optional binary logger
        logger = None
        bin_path = None
        if self.write_bin:
            tmpdir = self.bin_dir or tempfile.mkdtemp(prefix='picosim_')
            bin_path = os.path.join(tmpdir, 'flight.bin')
            logger = FlightLogger(flush_every=25, sync_every=1)
            # Monkey-patch: write directly to our temp path instead of /sd/
            logger._flight_dir = tmpdir
            logger._file = open(bin_path, 'wb')
            logger._sd_failed = False
            # Write file header
            logger._file.write(b'RKTLOG')
            logger._file.write(struct.pack('<HH', 2, _dl_mod.FRAME_SIZE))
            logger._file.flush()

        # Main loop
        for i in range(len(raw_data)):
            reading = raw_data[i]
            now_ms_int = int(now_ms)
            is_error = False

            if reading is SENSOR_FAULT:
                # Mirror main.py's except block — log error frame, continue
                is_error = True
                frame = {
                    'timestamp_ms': now_ms_int,
                    'state': prev_state,
                    'pressure_pa': 0.0,
                    'temperature_c': 0.0,
                    'alt_raw': 0.0,
                    'alt_filtered': 0.0,
                    'vel_filtered': 0.0,
                    'v_3v3_mv': 0, 'v_5v_mv': 0, 'v_9v_mv': 0,
                    'flags': 0x08,
                    'is_error': True,
                }
                if logger:
                    logger.write_frame(
                        timestamp_ms=now_ms_int, state=prev_state,
                        pressure_pa=0.0, temperature_c=0.0,
                        alt_raw=0.0, alt_filtered=0.0, vel_filtered=0.0,
                        v_3v3_mv=0, v_5v_mv=0, v_9v_mv=0, flags=0x08,
                    )
                frames.append(frame)
                now_ms += dt_ms
                continue

            pressure, temperature = reading

            # Pipeline: pressure → altitude → Kalman → FSM
            alt_raw = pressure_to_altitude(pressure, ground_pressure)
            alt_filt, vel_filt = kalman.update(alt_raw, dt)
            state = fsm.update(alt_filt, vel_filt, now_ms_int)

            # Voltages
            v3, v5, v9 = 3300, 5000, 9000
            if volt_iter:
                try:
                    v3, v5, v9 = next(volt_iter)
                except StopIteration:
                    volt_iter = None

            flags = 0

            # Track transitions
            if state != prev_state:
                transitions.append((now_ms_int, prev_state, state))
                if logger:
                    logger.notify_state_change(state)
                prev_state = state

            frame = {
                'timestamp_ms': now_ms_int,
                'state': state,
                'pressure_pa': pressure,
                'temperature_c': temperature,
                'alt_raw': alt_raw,
                'alt_filtered': alt_filt,
                'vel_filtered': vel_filt,
                'v_3v3_mv': v3, 'v_5v_mv': v5, 'v_9v_mv': v9,
                'flags': flags,
                'is_error': False,
            }

            if logger:
                logger.write_frame(
                    timestamp_ms=now_ms_int, state=state,
                    pressure_pa=pressure, temperature_c=temperature,
                    alt_raw=alt_raw, alt_filtered=alt_filt, vel_filtered=vel_filt,
                    v_3v3_mv=v3, v_5v_mv=v5, v_9v_mv=v9, flags=flags,
                )

            frames.append(frame)
            now_ms += dt_ms

        # Close logger
        if logger:
            logger.close()

        global _last_result
        result = SimResult(frames, transitions, fsm, bin_path=bin_path)
        _last_result = result
        return result
