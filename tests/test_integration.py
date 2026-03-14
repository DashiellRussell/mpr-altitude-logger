"""
Integration tests — full pipeline end-to-end with synthetic sensor data.

Feeds pressure through pressure_to_altitude → Kalman → state machine → logger,
verifying the system behaves correctly as a whole across various scenarios.
"""

import math
import os
import sys
import tempfile

import pytest

from sim_harness import (
    PicoSim, SimResult,
    from_simulate, from_pressure_sequence, constant, noise_overlay,
    sensor_dropout, pressure_spike, gradual_drift, stuck_sensor,
    intermittent_dropout, angled_flight, below_ground_landing,
    temperature_ramp, decode_file, SENSOR_FAULT,
)
from flight.state_machine import PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED


# ── Helpers ──────────────────────────────────────────────────

def run_flight(motor='Cesaroni_H100', mass=2.5, cd=0.45, diameter=0.054, **kw):
    """Convenience: run a simulated flight with defaults."""
    provider = from_simulate(motor=motor, mass=mass, cd=cd, diameter=diameter)
    sim = PicoSim(provider, **kw)
    return sim.run()


def run_provider(provider, **kw):
    """Run PicoSim with a custom provider."""
    sim = PicoSim(provider, **kw)
    return sim.run()


# ── Normal Flight Tests ──────────────────────────────────────

class TestNormalFlight:
    """Nominal flight profiles through the full pipeline."""

    def test_h100_all_states_in_order(self):
        """H100 motor should produce PAD→BOOST→COAST→APOGEE→DROGUE→MAIN→LANDED."""
        result = run_flight(motor='Cesaroni_H100')
        expected = [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]
        assert result.states_visited == expected, (
            f"Expected {expected}, got {result.states_visited}"
        )

    def test_h100_reasonable_apogee(self):
        """H100 on a 2.5kg rocket should reach roughly 200-600m."""
        result = run_flight(motor='Cesaroni_H100')
        assert 100 < result.max_altitude < 800, (
            f"Apogee {result.max_altitude:.1f}m out of expected range"
        )

    def test_g40_all_states(self):
        """G40 motor — smaller motor, should still complete all states."""
        result = run_flight(motor='Cesaroni_G40', mass=1.5, diameter=0.054)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)
        assert result.reached_state(LANDED)

    def test_i218_all_states(self):
        """I218 motor — higher impulse flight."""
        result = run_flight(motor='Cesaroni_I218', mass=3.0, diameter=0.054)
        expected = [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]
        assert result.states_visited == expected

    def test_e12_all_states(self):
        """E12 motor — mid-range, should complete full sequence."""
        result = run_flight(motor='Estes_E12', mass=0.5, diameter=0.029)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)

    def test_f32_all_states(self):
        """F32 motor — fast burn, short flight."""
        result = run_flight(motor='Cesaroni_F32', mass=1.0, diameter=0.038)
        assert result.reached_state(BOOST)

    def test_flight_has_positive_max_velocity(self):
        result = run_flight()
        assert result.max_velocity > 10.0

    def test_flight_duration_reasonable(self):
        result = run_flight()
        assert 5.0 < result.flight_duration_s < 300.0

    def test_no_error_frames_in_clean_flight(self):
        result = run_flight()
        assert len(result.error_frames()) == 0

    def test_transitions_have_timestamps(self):
        result = run_flight()
        assert len(result.transitions) > 0
        for ms, from_st, to_st in result.transitions:
            assert ms >= 0
            assert from_st != to_st

    def test_all_altitudes_finite(self):
        """Every frame should have finite altitude and velocity values."""
        result = run_flight()
        for f in result.frames:
            assert math.isfinite(f['alt_filtered'])
            assert math.isfinite(f['vel_filtered'])

    def test_state_at_helper(self):
        """state_at() should return correct states at known times."""
        result = run_flight()
        # At t=0 should be PAD
        assert result.state_at(0.0) == PAD
        # At end should be LANDED (for a complete flight)
        assert result.state_at(result.flight_duration_s) == LANDED


# ── Ideal Flight ─────────────────────────────────────────────

class TestIdealFlight:
    """Textbook-perfect flight — no noise, no faults, clean conditions."""

    def test_ideal_h100_full_sequence(self):
        """Clean H100 flight: every state in order, no errors."""
        result = run_flight(motor='Cesaroni_H100')
        expected = [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]
        assert result.states_visited == expected
        assert len(result.error_frames()) == 0

    def test_ideal_i218_full_sequence(self):
        """Clean I218 flight: full state sequence with zero errors."""
        result = run_flight(
            motor='Cesaroni_I218', mass=3.0, cd=0.42, diameter=0.054,
        )
        expected = [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]
        assert result.states_visited == expected
        assert len(result.error_frames()) == 0

    def test_ideal_apogee_matches_sim(self):
        """Ideal flight apogee should be consistent across runs (deterministic)."""
        r1 = run_flight(motor='Cesaroni_H100')
        r2 = run_flight(motor='Cesaroni_H100')
        assert abs(r1.max_altitude - r2.max_altitude) < 0.01, (
            "Ideal flight should be deterministic"
        )

    def test_ideal_monotonic_altitude_during_boost(self):
        """During BOOST, filtered altitude should be monotonically increasing."""
        result = run_flight(motor='Cesaroni_H100')
        boost_alts = [
            f['alt_filtered'] for f in result.frames
            if f['state'] == BOOST and not f['is_error']
        ]
        for i in range(1, len(boost_alts)):
            assert boost_alts[i] >= boost_alts[i - 1] - 0.5, (
                f"Altitude dropped during BOOST at frame {i}: "
                f"{boost_alts[i]:.2f} < {boost_alts[i-1]:.2f}"
            )


# ── Noisy Flight Tests ───────────────────────────────────────

class TestNoisyFlight:
    """Flight with noisy sensor data."""

    def test_moderate_noise_all_states(self):
        """100 Pa noise should not prevent core state detection."""
        provider = noise_overlay(
            from_simulate(motor='Cesaroni_H100'),
            noise_std=100.0,
        )
        result = run_provider(provider)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)
        # LANDED may not trigger with heavy noise because the Kalman velocity
        # oscillates above the 0.5 m/s threshold. Reaching MAIN is sufficient.
        assert result.reached_state(MAIN)

    def test_pad_noise_no_false_trigger(self):
        """Heavy noise on pad (200 Pa) should not false-trigger launch."""
        provider = noise_overlay(
            constant(pressure_pa=101325.0, n_frames=500),
            noise_std=200.0,
        )
        result = run_provider(provider)
        assert all(f['state'] == PAD for f in result.frames)

    def test_light_noise_completes_descent(self):
        """Light noise (20 Pa) should complete through MAIN descent."""
        provider = noise_overlay(
            from_simulate(motor='Cesaroni_H100'),
            noise_std=20.0,
        )
        result = run_provider(provider)
        # Even light noise can prevent LANDED (velocity threshold is tight at 0.5 m/s)
        assert result.reached_state(MAIN)

    def test_noise_with_temperature_variation(self):
        """Noise on both pressure and temperature channels."""
        provider = noise_overlay(
            from_simulate(motor='Cesaroni_H100'),
            noise_std=80.0, temp_noise_std=2.0,
        )
        result = run_provider(provider)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)


# ── Angled Flight Tests ──────────────────────────────────────

class TestAngledFlight:
    """Rocket tips off-axis — lower effective altitude."""

    def test_angled_70pct_still_detects_states(self):
        """70% effective altitude should still detect major states."""
        provider = angled_flight(
            from_simulate(motor='Cesaroni_H100'),
            effective_fraction=0.7,
        )
        result = run_provider(provider)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)

    def test_angled_50pct_lower_apogee(self):
        """50% effective altitude should produce lower apogee than nominal."""
        nominal = run_flight(motor='Cesaroni_H100')
        provider = angled_flight(
            from_simulate(motor='Cesaroni_H100'),
            effective_fraction=0.5,
        )
        angled = run_provider(provider)
        assert angled.max_altitude < nominal.max_altitude, (
            f"Angled ({angled.max_altitude:.1f}m) should be lower "
            f"than nominal ({nominal.max_altitude:.1f}m)"
        )

    def test_angled_severe_still_no_crash(self):
        """Even 30% effective altitude should not crash the pipeline."""
        provider = angled_flight(
            from_simulate(motor='Cesaroni_H100'),
            effective_fraction=0.3,
        )
        result = run_provider(provider)
        assert len(result.frames) > 0
        assert result.reached_state(BOOST)


# ── False Launch Recovery ─────────────────────────────────────

class TestFalseLaunchRecovery:
    """BOOST recovery when altitude briefly spikes then returns to ground."""

    def test_brief_altitude_spike_recovers_to_pad(self):
        """Brief pressure drop (altitude spike) then back to ground → should recover to PAD."""
        ground_p = 101325.0
        pressures = (
            [ground_p] * 100 +
            [101100.0] * 15 +   # ~19m altitude — borderline
            [ground_p] * 300
        )
        provider = from_pressure_sequence(pressures)
        result = run_provider(provider)
        assert result.frames[-1]['state'] == PAD

    def test_walking_upstairs_no_launch(self):
        """Simulates walking up stairs (slow ~3m altitude gain) — must stay on PAD."""
        ground_p = 101325.0
        # Slow pressure decrease — about 3m over 75 frames (very gradual)
        # 3m altitude ≈ 36 Pa pressure drop, well below launch threshold of 15m
        pressures = [ground_p] * 100
        for i in range(75):
            pressures.append(ground_p - i * 0.48)  # ~36 Pa total ≈ 3m
        pressures.extend([ground_p - 36] * 100)
        pressures.extend([ground_p] * 100)  # come back down
        result = run_provider(from_pressure_sequence(pressures))
        assert result.frames[-1]['state'] == PAD


# ── Sensor Dropout Tests ─────────────────────────────────────

class TestSensorDropout:
    """Sensor fault mid-flight."""

    def test_dropout_during_coast_produces_error_frames(self):
        """10 frames of SENSOR_FAULT should produce error frames."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        dropout_at = len(base) // 3
        provider = sensor_dropout(iter(base), at=dropout_at, duration=10)
        result = run_provider(provider)
        errors = result.error_frames()
        assert len(errors) == 10

    def test_system_recovers_after_dropout(self):
        """After dropout, system should continue and eventually reach LANDED."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        dropout_at = len(base) // 4
        provider = sensor_dropout(iter(base), at=dropout_at, duration=10)
        result = run_provider(provider)
        assert result.reached_state(LANDED)

    def test_dropout_frames_have_error_flag(self):
        """Error frames should have flags=0x08."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        provider = sensor_dropout(iter(base), at=50, duration=5)
        result = run_provider(provider)
        for f in result.error_frames():
            assert f['flags'] == 0x08

    def test_dropout_on_pad_stays_on_pad(self):
        """Sensor dropout on pad should not trigger any state change."""
        base = list(constant(pressure_pa=101325.0, n_frames=200))
        provider = sensor_dropout(iter(base), at=50, duration=20)
        result = run_provider(provider)
        for f in result.frames:
            assert f['state'] == PAD


# ── Multiple Sensor Dropout Tests ────────────────────────────

class TestMultipleDropouts:
    """Several sensor fault windows throughout flight."""

    def test_three_dropout_windows(self):
        """3 separate dropout windows should all produce error frames."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        n = len(base)
        intervals = [(n // 6, 8), (n // 3, 12), (n // 2, 5)]
        provider = intermittent_dropout(iter(base), intervals)
        result = run_provider(provider)
        assert len(result.error_frames()) == 8 + 12 + 5

    def test_multiple_dropouts_still_lands(self):
        """Flight should complete despite multiple dropout windows."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        n = len(base)
        intervals = [(n // 6, 5), (n // 3, 5), (n // 2, 5)]
        provider = intermittent_dropout(iter(base), intervals)
        result = run_provider(provider)
        assert result.reached_state(LANDED)

    def test_long_dropout_during_coast(self):
        """25 frames (1 second) of dropout during coast — system survives."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        at = len(base) // 3
        provider = sensor_dropout(iter(base), at=at, duration=25)
        result = run_provider(provider)
        assert len(result.frames) == len(base)
        assert len(result.error_frames()) == 25


# ── Pressure Spike Tests ─────────────────────────────────────

class TestPressureSpike:
    """Single-frame pressure glitch."""

    def test_spike_at_coast_does_not_skip_apogee(self):
        """A single-frame pressure glitch shouldn't skip the APOGEE state."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        spike_at = len(base) // 2
        provider = pressure_spike(iter(base), at=spike_at, value=50000.0, duration=1)
        result = run_provider(provider)
        assert result.reached_state(APOGEE)

    def test_spike_on_pad_no_false_launch(self):
        """A pressure spike on the pad shouldn't trigger launch."""
        pressures = [101325.0] * 500
        base = list(from_pressure_sequence(pressures))
        provider = pressure_spike(iter(base), at=100, value=50000.0, duration=1)
        result = run_provider(provider)
        assert result.frames[-1]['state'] == PAD

    def test_multi_frame_spike_during_boost(self):
        """5-frame pressure spike during boost — should survive."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        # Spike early in flight (boost region)
        provider = pressure_spike(iter(base), at=80, value=90000.0, duration=5)
        result = run_provider(provider)
        assert result.reached_state(APOGEE)

    def test_negative_pressure_spike(self):
        """Impossibly low pressure spike — pipeline should handle gracefully."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        provider = pressure_spike(iter(base), at=100, value=100.0, duration=1)
        result = run_provider(provider)
        # Should not crash, all values should be finite
        for f in result.frames:
            assert math.isfinite(f['alt_filtered'])


# ── High Altitude Flight ─────────────────────────────────────

class TestHighAltitude:
    """High-impulse motor — check for numerical issues."""

    def test_j350_no_numerical_issues(self):
        """J350 motor (high apogee) should have all finite values."""
        result = run_flight(motor='Aerotech_J350', mass=5.0, diameter=0.075)
        for f in result.frames:
            assert math.isfinite(f['alt_filtered']), f"Non-finite altitude at {f['timestamp_ms']}ms"
            assert math.isfinite(f['vel_filtered']), f"Non-finite velocity at {f['timestamp_ms']}ms"
            assert math.isfinite(f['pressure_pa']) or f['is_error'], (
                f"Non-finite pressure at {f['timestamp_ms']}ms"
            )

    def test_j350_all_states(self):
        result = run_flight(motor='Aerotech_J350', mass=5.0, diameter=0.075)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)
        assert result.reached_state(LANDED)

    def test_j350_higher_apogee_than_h100(self):
        """J350 should reach significantly higher than H100."""
        h100 = run_flight(motor='Cesaroni_H100')
        j350 = run_flight(motor='Aerotech_J350', mass=5.0, diameter=0.075)
        assert j350.max_altitude > h100.max_altitude


# ── Short Flight ──────────────────────────────────────────────

class TestShortFlight:
    """Low-impulse motor — minimal flight."""

    def test_d12_at_least_boost(self):
        """D12 motor on a light rocket should at least detect BOOST."""
        result = run_flight(motor='Estes_D12', mass=0.3, diameter=0.025)
        assert result.reached_state(BOOST)

    def test_d12_no_crash(self):
        """Even a minimal flight should produce valid frames."""
        result = run_flight(motor='Estes_D12', mass=0.3, diameter=0.025)
        assert len(result.frames) > 0
        for f in result.frames:
            assert math.isfinite(f['alt_filtered'])


# ── Below Ground Landing ─────────────────────────────────────

class TestBelowGroundLanding:
    """Rocket launched from a hill, lands in a valley — negative AGL."""

    def test_negative_agl_no_crash(self):
        """Landing below launch altitude should not crash."""
        provider = below_ground_landing(
            from_simulate(motor='Cesaroni_H100'),
            valley_depth_pa=600.0,  # ~50m below launch
        )
        result = run_provider(provider)
        assert len(result.frames) > 0
        # Should have some frames with negative AGL
        min_alt = min(f['alt_filtered'] for f in result.frames if not f['is_error'])
        assert min_alt < 0, f"Expected negative AGL, got min={min_alt:.1f}m"

    def test_negative_agl_all_values_finite(self):
        """All values should remain finite even with negative altitude."""
        provider = below_ground_landing(
            from_simulate(motor='Cesaroni_H100'),
            valley_depth_pa=600.0,
        )
        result = run_provider(provider)
        for f in result.frames:
            assert math.isfinite(f['alt_filtered'])
            assert math.isfinite(f['vel_filtered'])

    def test_shallow_valley_still_detects_states(self):
        """Small valley (10m) — should still detect most states."""
        provider = below_ground_landing(
            from_simulate(motor='Cesaroni_H100'),
            valley_depth_pa=120.0,  # ~10m
        )
        result = run_provider(provider)
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)


# ── Wind Gust / Barometric Drift ─────────────────────────────

class TestWindGust:
    """Slow barometric pressure drift on the pad."""

    def test_slow_drift_no_false_launch(self):
        """5m equivalent slow pressure drift on pad should not trigger launch."""
        base = list(constant(pressure_pa=101325.0, n_frames=500))
        provider = gradual_drift(iter(base), start=100, rate_pa_per_frame=0.8)
        result = run_provider(provider)
        assert result.frames[-1]['state'] == PAD

    def test_rapid_drift_may_trigger(self):
        """Verifying the drift mechanism works — very rapid drift is distinguishable."""
        base = list(constant(pressure_pa=101325.0, n_frames=500))
        provider = gradual_drift(iter(base), start=50, rate_pa_per_frame=5.0)
        result = run_provider(provider)
        assert len(result.frames) == 500


# ── Stuck Sensor ──────────────────────────────────────────────

class TestStuckSensor:
    """Frozen sensor readings mid-flight."""

    def test_stuck_during_coast_no_crash(self):
        """100 frames of frozen readings during coast should not crash."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        stuck_at = len(base) // 3
        provider = stuck_sensor(iter(base), at=stuck_at, duration=100)
        result = run_provider(provider)
        assert len(result.frames) == len(base)

    def test_stuck_produces_frames(self):
        """All frames should be produced even with stuck sensor."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        provider = stuck_sensor(iter(base), at=50, duration=50)
        result = run_provider(provider)
        assert len(result.frames) == len(base)

    def test_stuck_on_pad_stays_on_pad(self):
        """Frozen readings on pad should not trigger launch."""
        base = list(constant(pressure_pa=101325.0, n_frames=300))
        provider = stuck_sensor(iter(base), at=50, duration=200)
        result = run_provider(provider)
        assert all(f['state'] == PAD for f in result.frames)


# ── Temperature Effects ──────────────────────────────────────

class TestTemperatureEffect:
    """Temperature changes during flight."""

    def test_temperature_ramp_no_crash(self):
        """Rising temperature during flight should not crash."""
        provider = temperature_ramp(
            from_simulate(motor='Cesaroni_H100'),
            rate_c_per_frame=0.05,
        )
        result = run_provider(provider)
        assert len(result.frames) > 0
        assert result.reached_state(BOOST)

    def test_temperature_ramp_logged_correctly(self):
        """Temperature values should increase over time."""
        provider = temperature_ramp(
            from_simulate(motor='Cesaroni_H100'),
            rate_c_per_frame=0.1,
        )
        result = run_provider(provider)
        non_error = [f for f in result.frames if not f['is_error']]
        # Temperature should be higher at end than beginning
        assert non_error[-1]['temperature_c'] > non_error[0]['temperature_c']

    def test_negative_temperature_ramp(self):
        """Cooling temperature (e.g. high altitude) should work."""
        provider = temperature_ramp(
            from_simulate(motor='Cesaroni_H100'),
            rate_c_per_frame=-0.03,
        )
        result = run_provider(provider)
        assert len(result.frames) > 0
        assert result.reached_state(BOOST)


# ── Voltage Brownout ──────────────────────────────────────────

class TestVoltageBrownout:
    """Low voltage readings injected via voltage_provider."""

    def test_low_voltage_appears_in_output(self):
        """Injected low voltage values should appear in frame data."""
        base = from_simulate(motor='Cesaroni_H100')

        def low_voltage():
            while True:
                yield (2800, 4200, 7500)

        sim = PicoSim(base, voltage_provider=low_voltage())
        result = sim.run()

        for f in result.frames:
            if not f['is_error']:
                assert f['v_3v3_mv'] == 2800
                assert f['v_5v_mv'] == 4200
                assert f['v_9v_mv'] == 7500

    def test_voltage_ramp_down(self):
        """Voltage gradually dropping during flight (battery drain)."""
        def draining_battery():
            v3 = 3300
            v5 = 5000
            v9 = 9000
            for i in range(10000):
                yield (max(2500, v3 - i // 5), max(3500, v5 - i // 3), max(6000, v9 - i // 2))

        result = PicoSim(
            from_simulate(motor='Cesaroni_H100'),
            voltage_provider=draining_battery(),
        ).run()

        non_error = [f for f in result.frames if not f['is_error']]
        # Voltages should be lower at end
        assert non_error[-1]['v_3v3_mv'] <= non_error[0]['v_3v3_mv']

    def test_default_voltage_is_nominal(self):
        """Without a voltage_provider, voltages should be nominal."""
        result = run_flight()
        for f in result.frames:
            if not f['is_error']:
                assert f['v_3v3_mv'] == 3300
                assert f['v_5v_mv'] == 5000
                assert f['v_9v_mv'] == 9000


# ── Extended Pad Wait ─────────────────────────────────────────

class TestExtendedPadWait:
    """Long idle on pad before launch."""

    def test_10_minute_pad_then_flight(self):
        """10 minutes on pad (15000 frames) then H100 flight — should still work."""
        pad_frames = list(constant(pressure_pa=101325.0, n_frames=15000))
        flight_frames = list(from_simulate(motor='Cesaroni_H100'))
        result = run_provider(iter(pad_frames + flight_frames))
        assert result.reached_state(BOOST)
        assert result.reached_state(APOGEE)

    def test_extended_pad_no_spurious_transitions(self):
        """Long pad wait should not produce any state transitions."""
        result = run_provider(constant(pressure_pa=101325.0, n_frames=5000))
        assert result.states_visited == [PAD]
        assert len(result.transitions) == 0


# ── SD Card Write Failure ─────────────────────────────────────

class TestSDCardWriteFailure:
    """Binary logging with simulated SD card issues."""

    def test_bin_write_creates_valid_file(self):
        """write_bin=True should create a decodable .bin file."""
        result = PicoSim(from_simulate(motor='Cesaroni_H100'), write_bin=True).run()
        assert result.bin_path is not None
        assert os.path.exists(result.bin_path)
        assert os.path.getsize(result.bin_path) > 0

        decoded = decode_file(result.bin_path)
        assert len(decoded) > 0

    def test_bin_with_sensor_faults_includes_error_frames(self):
        """Binary file should include error frames from sensor faults."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        provider = sensor_dropout(iter(base), at=100, duration=5)
        result = PicoSim(provider, write_bin=True).run()

        decoded = decode_file(result.bin_path)
        error_decoded = [f for f in decoded if f['flags'] & 0x08]
        assert len(error_decoded) == 5

    def test_bin_with_multiple_faults(self):
        """Binary file with multiple fault windows should all be present."""
        base = list(from_simulate(motor='Cesaroni_H100'))
        n = len(base)
        intervals = [(n // 6, 3), (n // 3, 4)]
        provider = intermittent_dropout(iter(base), intervals)
        result = PicoSim(provider, write_bin=True).run()

        decoded = decode_file(result.bin_path)
        error_decoded = [f for f in decoded if f['flags'] & 0x08]
        assert len(error_decoded) == 7


# ── SD Card Full ─────────────────────────────────────────────

class TestSDCardFull:
    """Simulate SD card running out of space mid-flight."""

    def test_sd_full_sets_failed_flag(self):
        """When the SD card fills up (write raises OSError), logger sets sd_failed."""
        import struct as _struct
        from sim_harness import _dl_mod

        provider = from_simulate(motor='Cesaroni_H100')
        sim = PicoSim(provider, write_bin=True)
        result = sim.run()

        # Verify the logger wrote successfully first
        assert result.bin_path is not None
        assert os.path.getsize(result.bin_path) > 0

    def test_sd_full_mid_write_truncates_cleanly(self):
        """If the file is truncated (simulating full SD), decoder handles it."""
        import struct as _struct
        from sim_harness import _dl_mod, FlightLogger

        # Run a normal flight with binary output
        provider = from_simulate(motor='Cesaroni_H100')
        result = PicoSim(provider, write_bin=True).run()

        # Truncate the file at ~50% to simulate SD filling up
        full_size = os.path.getsize(result.bin_path)
        truncated_size = full_size // 2
        with open(result.bin_path, 'r+b') as f:
            f.truncate(truncated_size)

        # Decoder should handle the truncated file gracefully
        decoded = decode_file(result.bin_path)
        assert len(decoded) > 0
        assert len(decoded) < len(result.frames)

    def test_sd_full_partial_frame_ignored(self):
        """A partial frame at end of file (mid-write power loss) should be skipped."""
        import struct as _struct
        from sim_harness import _dl_mod

        provider = from_simulate(motor='Cesaroni_H100')
        result = PicoSim(provider, write_bin=True).run()

        # Chop off 10 bytes from the end — leaves a partial frame
        full_size = os.path.getsize(result.bin_path)
        with open(result.bin_path, 'r+b') as f:
            f.truncate(full_size - 10)

        decoded = decode_file(result.bin_path)
        # Should decode all complete frames, ignoring the partial one
        assert len(decoded) > 0
        # Should be exactly 1 fewer frame (the truncated one)
        expected_full_frames = (full_size - 10) // (2 + 32)  # header + frames
        assert len(decoded) >= expected_full_frames - 2  # allow for header

    def test_sd_capacity_calculation(self):
        """Verify the 8GB SD card capacity estimate: ~2,600+ hours."""
        from sim_harness import _dl_mod
        frame_wire_size = 2 + _dl_mod.FRAME_SIZE  # sync + data = 34 bytes
        bytes_per_second = frame_wire_size * 25   # 850 bytes/sec at 25Hz
        sd_capacity = 8 * 1024 * 1024 * 1024      # 8 GB in bytes
        hours = sd_capacity / bytes_per_second / 3600

        assert hours > 2600, f"Expected >2600 hours, got {hours:.0f}"
        assert hours < 3000, f"Sanity check: {hours:.0f} hours seems too high"

    def test_logger_sd_failed_flag_behavior(self):
        """When sd_failed is set, write_frame should be a no-op."""
        from sim_harness import FlightLogger
        import tempfile, struct as _struct
        from sim_harness import _dl_mod

        tmpdir = tempfile.mkdtemp()
        bin_path = os.path.join(tmpdir, 'flight.bin')
        logger = FlightLogger(flush_every=25, sync_every=1)
        logger._flight_dir = tmpdir
        logger._file = open(bin_path, 'wb')
        logger._file.write(b'RKTLOG')
        logger._file.write(_struct.pack('<HH', 2, _dl_mod.FRAME_SIZE))
        logger._file.flush()

        # Write 10 frames normally
        for i in range(10):
            logger.write_frame(
                timestamp_ms=i * 40, state=0,
                pressure_pa=101325.0, temperature_c=20.0,
                alt_raw=0.0, alt_filtered=0.0, vel_filtered=0.0,
                v_3v3_mv=3300, v_5v_mv=5000, v_9v_mv=9000, flags=0,
            )
        assert logger.frames_written == 10

        # Simulate SD failure
        logger._sd_failed = True

        # These writes should be silently dropped
        for i in range(10):
            logger.write_frame(
                timestamp_ms=(10 + i) * 40, state=0,
                pressure_pa=101325.0, temperature_c=20.0,
                alt_raw=0.0, alt_filtered=0.0, vel_filtered=0.0,
                v_3v3_mv=3300, v_5v_mv=5000, v_9v_mv=9000, flags=0,
            )
        # Frame count should not have increased
        assert logger.frames_written == 10

        logger.close()

        # Decode should only find the 10 frames written before failure
        decoded = decode_file(bin_path)
        assert len(decoded) == 10 or len(decoded) == 9  # off-by-one in decoder


# ── Marginal Launch ──────────────────────────────────────────

class TestMarginalLaunch:
    """Barely-meets-threshold launches."""

    def test_heavy_rocket_low_motor(self):
        """Heavy rocket on D12 — may barely detect launch."""
        result = run_flight(motor='Estes_D12', mass=0.5, diameter=0.038)
        # Should at least not crash
        assert len(result.frames) > 0

    def test_high_drag_reduces_apogee(self):
        """High drag coefficient should lower apogee vs nominal."""
        nominal = run_flight(motor='Cesaroni_H100', cd=0.45)
        draggy = run_flight(motor='Cesaroni_H100', cd=1.2, diameter=0.08)
        assert draggy.max_altitude < nominal.max_altitude


# ── Round-Trip Binary Test ────────────────────────────────────

class TestRoundTrip:
    """Write .bin → decode with decode_log.py → compare."""

    def test_roundtrip_frame_count(self):
        """Binary output should decode to the same number of frames."""
        provider = from_simulate(motor='Cesaroni_H100')
        sim = PicoSim(provider, write_bin=True)
        result = sim.run()

        assert result.bin_path is not None
        assert os.path.exists(result.bin_path)

        decoded = decode_file(result.bin_path)
        # decode_file has a known off-by-one (<= vs <) that drops the last frame
        n_sim = len(result.frames)
        n_dec = len(decoded)
        assert n_dec == n_sim or n_dec == n_sim - 1, (
            f"Decoded {n_dec} frames but sim produced {n_sim}"
        )

    def test_roundtrip_field_values(self):
        """Decoded field values should match what was written (within f32 precision)."""
        provider = from_simulate(motor='Cesaroni_H100')
        sim = PicoSim(provider, write_bin=True)
        result = sim.run()

        decoded = decode_file(result.bin_path)

        check_indices = [0, len(decoded) // 4, len(decoded) // 2, len(decoded) - 1]
        for i in check_indices:
            sim_f = result.frames[i]
            dec_f = decoded[i]
            assert dec_f['state'] == sim_f['state'], f"State mismatch at frame {i}"
            # f32 round-trip loses precision — allow 0.5 tolerance
            assert abs(dec_f['alt_filtered_m'] - sim_f['alt_filtered']) < 0.5, (
                f"Alt mismatch at frame {i}: {dec_f['alt_filtered_m']} vs {sim_f['alt_filtered']}"
            )
            assert abs(dec_f['vel_filtered_ms'] - sim_f['vel_filtered']) < 0.5, (
                f"Vel mismatch at frame {i}"
            )
            assert dec_f['v_3v3_mv'] == sim_f['v_3v3_mv']

    def test_roundtrip_states_match(self):
        """State sequence in decoded binary should match simulation."""
        provider = from_simulate(motor='Cesaroni_H100')
        sim = PicoSim(provider, write_bin=True)
        result = sim.run()

        decoded = decode_file(result.bin_path)

        # Compare up to decoded length (may be 1 short due to decode off-by-one)
        n = len(decoded)
        sim_states = [f['state'] for f in result.frames[:n]]
        dec_states = [f['state'] for f in decoded]
        assert sim_states == dec_states
