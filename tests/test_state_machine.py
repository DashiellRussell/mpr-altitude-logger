"""Tests for the flight state machine."""

import config
from flight.state_machine import (
    FlightStateMachine, PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED,
)


def make_fsm():
    fsm = FlightStateMachine()
    fsm.set_ground_reference(0.0)
    return fsm


def advance_ms(fsm, alt, vel, start_ms, count, interval_ms=40):
    """Run fsm.update() `count` times, returning the final state."""
    state = None
    for i in range(count):
        state = fsm.update(alt, vel, start_ms + i * interval_ms)
    return state


class TestFullFlightSequence:
    """Verify PAD → BOOST → COAST → APOGEE → DROGUE → MAIN → LANDED."""

    def test_complete_flight(self):
        fsm = make_fsm()
        ms = 0

        # PAD — no launch yet
        assert fsm.update(0.0, 0.0, ms) == PAD

        # Trigger launch: alt > 15m, vel > 10 m/s sustained for 0.5s
        ms = 1000
        for i in range(15):  # 15 * 40ms = 600ms > 500ms window
            state = fsm.update(20.0, 15.0, ms + i * 40)
        assert state == BOOST

        # BOOST → COAST: velocity drops significantly from peak
        # Must be past recovery window (2s after launch)
        ms = 4000
        fsm.max_vel = 100.0
        state = fsm.update(500.0, 97.0, ms)  # still boosting (drop=3 < threshold=5)
        assert state == BOOST
        state = fsm.update(500.0, 80.0, ms + 40)  # 100 - 80 = 20 > 5 threshold
        assert state == COAST

        # COAST → APOGEE: velocity below threshold for APOGEE_CONFIRM_COUNT frames
        ms = 5000
        for i in range(config.APOGEE_CONFIRM_COUNT):
            state = fsm.update(1000.0, 1.0, ms + i * 40)
        assert state == APOGEE

        # APOGEE → DROGUE after dwell frames
        ms = 6000
        for i in range(config.APOGEE_DWELL_FRAMES):
            state = fsm.update(990.0, -5.0, ms + i * 40)
        assert state == DROGUE

        # DROGUE → MAIN at fraction of max altitude
        ms = 8000
        max_agl = fsm.max_alt - fsm.ground_alt
        low_alt = max_agl * config.MAIN_CHUTE_FRACTION * 0.9  # below threshold
        state = fsm.update(low_alt, -3.0, ms)
        assert state == MAIN

        # MAIN → LANDED: near-zero velocity sustained
        ms = 10000
        for i in range(int(config.LANDED_CONFIRM_SECONDS * 1000 / 40) + 2):
            state = fsm.update(0.5, 0.1, ms + i * 40)
        assert state == LANDED


class TestApogeeDwell:
    """APOGEE must stay for APOGEE_DWELL_FRAMES before transitioning to DROGUE."""

    def test_stays_in_apogee_during_dwell(self):
        fsm = make_fsm()
        fsm.state = APOGEE
        fsm.apogee_time = 1000

        # Should stay in APOGEE for dwell - 1 frames
        for i in range(config.APOGEE_DWELL_FRAMES - 1):
            state = fsm.update(500.0, -2.0, 2000 + i * 40)
            assert state == APOGEE

        # On the dwell-th frame, should transition
        state = fsm.update(500.0, -2.0, 2000 + (config.APOGEE_DWELL_FRAMES - 1) * 40)
        assert state == DROGUE


class TestBoostRecoverySustained:
    """BOOST recovery requires consecutive frames below threshold, not single-frame."""

    def test_single_frame_below_no_reset(self):
        """One frame below recovery alt should NOT reset to PAD."""
        fsm = make_fsm()
        fsm.state = BOOST
        fsm.launch_time = 1000

        # One frame below threshold
        state = fsm.update(5.0, 2.0, 1100)
        assert state == BOOST  # should NOT reset on single frame

    def test_sustained_below_resets_to_pad(self):
        """BOOST_RECOVERY_COUNT consecutive frames below threshold resets to PAD."""
        fsm = make_fsm()
        fsm.state = BOOST
        fsm.launch_time = 1000

        ms = 1100
        for i in range(config.BOOST_RECOVERY_COUNT):
            state = fsm.update(5.0, 2.0, ms + i * 40)

        assert state == PAD

    def test_intermittent_above_resets_counter(self):
        """If altitude goes above threshold between low frames, counter resets."""
        fsm = make_fsm()
        fsm.state = BOOST
        fsm.launch_time = 1000

        ms = 1100
        # One frame below
        fsm.update(5.0, 2.0, ms)
        # One frame above — should reset counter
        fsm.update(20.0, 15.0, ms + 40)
        # Two more below — shouldn't be enough (counter reset)
        fsm.update(5.0, 2.0, ms + 80)
        state = fsm.update(5.0, 2.0, ms + 120)

        # With BOOST_RECOVERY_COUNT=3, after reset we only had 2 consecutive frames
        if config.BOOST_RECOVERY_COUNT > 2:
            assert state == BOOST


class TestCoastTimeout:
    """Force APOGEE if stuck in COAST too long."""

    def test_coast_timeout_forces_apogee(self):
        fsm = make_fsm()
        fsm.state = COAST
        fsm.coast_start = 1000

        timeout_ms = int(config.COAST_TIMEOUT * 1000) + 1000
        # Keep velocity above apogee threshold so normal detection doesn't trigger
        state = fsm.update(500.0, 5.0, timeout_ms + 1001)
        assert state == APOGEE


class TestLandingDetection:
    """Landing requires sustained near-zero velocity."""

    def test_landing_requires_sustained_zero_vel(self):
        fsm = make_fsm()
        fsm.state = MAIN
        fsm.max_alt = 500.0

        ms = 10000
        # Not long enough
        for i in range(5):
            state = fsm.update(1.0, 0.1, ms + i * 40)
        assert state == MAIN  # shouldn't land yet

        # Long enough
        total_frames = int(config.LANDED_CONFIRM_SECONDS * 1000 / 40) + 2
        for i in range(total_frames):
            state = fsm.update(1.0, 0.1, ms + i * 40)
        assert state == LANDED

    def test_velocity_spike_resets_counter(self):
        fsm = make_fsm()
        fsm.state = MAIN
        fsm.max_alt = 500.0

        ms = 10000
        # Almost enough quiet time
        almost = int(config.LANDED_CONFIRM_SECONDS * 1000 / 40) - 2
        for i in range(almost):
            fsm.update(1.0, 0.1, ms + i * 40)

        # Spike resets counter
        fsm.update(1.0, 5.0, ms + almost * 40)

        # Need full duration again
        state = fsm.update(1.0, 0.1, ms + (almost + 1) * 40)
        assert state == MAIN  # not landed yet
