"""Tests for the 1D Kalman filter."""

import config
from flight.kalman import AltitudeKalman


class TestKalmanBasics:
    """Basic Kalman filter behavior."""

    def test_initial_update_sets_state(self):
        kf = AltitudeKalman()
        alt, vel = kf.update(100.0, 0.04)
        assert alt == 100.0
        assert vel == 0.0

    def test_dt_zero_noop(self):
        kf = AltitudeKalman()
        kf.reset(50.0)
        alt, vel = kf.update(60.0, 0.0)
        assert alt == 50.0  # no change
        assert vel == 0.0

    def test_dt_negative_noop(self):
        kf = AltitudeKalman()
        kf.reset(50.0)
        alt, vel = kf.update(60.0, -0.1)
        assert alt == 50.0
        assert vel == 0.0

    def test_reset_clears_state(self):
        kf = AltitudeKalman()
        kf.update(100.0, 0.04)
        kf.update(200.0, 0.04)
        kf.reset(0.0)
        assert kf.altitude == 0.0
        assert kf.velocity == 0.0


class TestKalmanConvergence:
    """Filter should converge to constant measurements."""

    def test_constant_altitude_convergence(self):
        kf = AltitudeKalman()
        target = 100.0
        for _ in range(200):
            alt, vel = kf.update(target, 0.04)
        assert abs(alt - target) < 0.5
        assert abs(vel) < 0.5

    def test_step_response_tracking(self):
        kf = AltitudeKalman()
        # Settle at 0
        for _ in range(50):
            kf.update(0.0, 0.04)
        # Step to 100
        for _ in range(200):
            alt, vel = kf.update(100.0, 0.04)
        assert abs(alt - 100.0) < 1.0

    def test_simulated_flight_profile(self):
        """Ascent → coast → descent should produce sensible estimates."""
        kf = AltitudeKalman()
        dt = 0.04  # 25 Hz

        # Ascent (0-3s): climbing at ~50 m/s
        for i in range(75):
            t = i * dt
            measured = 50.0 * t
            alt, vel = kf.update(measured, dt)

        assert alt > 50.0  # should be well above ground
        assert vel > 20.0  # should detect upward velocity

        # Coast (3-6s): decelerating, peak around 150m
        peak = alt
        for i in range(75):
            t = 3.0 + i * dt
            measured = 150.0 - 0.5 * 9.8 * (t - 3.0)**2 + 150.0 - 150.0
            measured = 150.0 - 4.9 * (t - 3.0)**2
            alt, vel = kf.update(measured, dt)

        # Velocity should have decreased
        assert vel < 20.0

        # Descent (6-10s): falling
        for i in range(100):
            t = 6.0 + i * dt
            measured = max(0, 150.0 - 4.9 * (t - 3.0)**2)
            alt, vel = kf.update(measured, dt)

        assert vel < 0  # falling


class TestKalmanNumericalStability:
    """Covariance clamping prevents numerical blowup."""

    def test_negative_covariance_prevention(self):
        """Extreme innovation shouldn't produce negative covariance diagonal."""
        kf = AltitudeKalman()
        kf.reset(0.0)

        # Feed extreme alternating values to stress the filter
        for i in range(1000):
            val = 1e6 if i % 2 == 0 else -1e6
            kf.update(val, 0.04)

        # Diagonal covariance must never be negative
        assert kf.p00 >= 0.0
        assert kf.p11 >= 0.0

    def test_very_small_dt(self):
        """Very small dt shouldn't cause division issues."""
        kf = AltitudeKalman()
        kf.reset(100.0)
        alt, vel = kf.update(100.0, 1e-8)
        # Should return finite values
        assert abs(alt) < 1e10
        assert abs(vel) < 1e10
