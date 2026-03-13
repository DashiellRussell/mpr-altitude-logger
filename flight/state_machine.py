"""
Flight state machine — automatic phase detection for data logging.

States:
    PAD     → sitting on the pad, recording ground reference
    BOOST   → motor burning, rapid altitude gain
    COAST   → motor burnout, still ascending
    APOGEE  → peak altitude detected
    DROGUE  → descending under drogue chute
    MAIN    → under main chute (altitude trigger)
    LANDED  → on the ground

All states are tracked for logging purposes only — no deployment hardware.
Transitions are based on Kalman-filtered altitude and velocity.
"""

import time
import config


# Flight state enum
PAD = 0
BOOST = 1
COAST = 2
APOGEE = 3
DROGUE = 4
MAIN = 5
LANDED = 6

STATE_NAMES = ["PAD", "BOOST", "COAST", "APOGEE", "DROGUE", "MAIN", "LANDED"]


class FlightStateMachine:
    """
    Determines current flight phase from filtered sensor data.
    All states are for logging — no deployment actions.
    """

    def __init__(self):
        self.state = PAD

        # Tracking
        self.ground_alt = 0.0
        self.max_alt = 0.0
        self.max_vel = 0.0
        self.launch_time = 0
        self.apogee_time = 0
        self.coast_start = 0

        # Detection counters
        self._apogee_count = 0
        self._landed_start = 0
        self._launch_alt_start = 0.0
        self._launch_time_start = 0

    def set_ground_reference(self, alt):
        """Set ground-level altitude (call after averaging on pad)."""
        self.ground_alt = alt

    def update(self, alt, vel, now_ms):
        """
        Run state machine with latest filtered data.

        Args:
            alt: Kalman-filtered altitude AGL (m)
            vel: Kalman-filtered vertical velocity (m/s, positive=up)
            now_ms: current time in ms (time.ticks_ms)

        Returns:
            state (int)
        """
        agl = alt - self.ground_alt

        # Only track maxima after confirmed launch (avoid pollution from pad handling)
        if self.state >= BOOST:
            if alt > self.max_alt:
                self.max_alt = alt
            if vel > self.max_vel:
                self.max_vel = vel

        if self.state == PAD:
            # Detect launch: sustained altitude gain AND velocity above threshold
            if agl > config.LAUNCH_ALT_THRESHOLD and vel > config.LAUNCH_VEL_THRESHOLD:
                if self._launch_time_start == 0:
                    self._launch_time_start = now_ms
                elif time.ticks_diff(now_ms, self._launch_time_start) > config.LAUNCH_DETECT_WINDOW * 1000:
                    self.state = BOOST
                    self.launch_time = now_ms
                    self.max_alt = alt
                    self.max_vel = vel
            else:
                self._launch_time_start = 0

        elif self.state == BOOST:
            # False launch recovery: if altitude drops back near ground early in "boost"
            if time.ticks_diff(now_ms, self.launch_time) < config.BOOST_RECOVERY_WINDOW * 1000:
                if agl < config.BOOST_RECOVERY_ALT:
                    self.state = PAD
                    self.launch_time = 0
                    self.max_alt = 0.0
                    self.max_vel = 0.0
                    self._launch_time_start = 0
            # Normal burnout detection (only after recovery window closes)
            elif self.max_vel > 0 and vel < self.max_vel - config.COAST_VEL_THRESHOLD:
                self.state = COAST
                self.coast_start = now_ms

        elif self.state == COAST:
            # Detect apogee: velocity near zero or negative
            if vel < config.APOGEE_VEL_THRESHOLD:
                self._apogee_count += 1
                if self._apogee_count >= config.APOGEE_CONFIRM_COUNT:
                    self.state = APOGEE
                    self.apogee_time = now_ms
            else:
                self._apogee_count = 0
            # Timeout: force apogee if stuck in COAST (noisy filter never crossing threshold)
            if self.coast_start and time.ticks_diff(now_ms, self.coast_start) > config.COAST_TIMEOUT * 1000:
                self.state = APOGEE
                self.apogee_time = now_ms

        elif self.state == APOGEE:
            # Transition to descent immediately
            self.state = DROGUE

        elif self.state == DROGUE:
            # Transition to MAIN at fraction of max altitude AGL (works for any apogee height)
            max_agl = self.max_alt - self.ground_alt
            if max_agl > 0 and agl <= max_agl * config.MAIN_CHUTE_FRACTION and vel < 0:
                self.state = MAIN
            # Also check for landing directly from DROGUE (safety net)
            self._check_landed(vel, now_ms)

        elif self.state == MAIN:
            # Detect landing: near-zero velocity for sustained period
            self._check_landed(vel, now_ms)

        elif self.state == LANDED:
            pass  # Terminal state

        return self.state

    def _check_landed(self, vel, now_ms):
        """Check for landing: near-zero velocity sustained over confirmation window."""
        if abs(vel) < config.LANDED_VEL_THRESHOLD:
            if self._landed_start == 0:
                self._landed_start = now_ms
            elif time.ticks_diff(now_ms, self._landed_start) > config.LANDED_CONFIRM_SECONDS * 1000:
                self.state = LANDED
        else:
            self._landed_start = 0

    @property
    def state_name(self):
        return STATE_NAMES[self.state]

    def get_stats(self):
        """Return flight statistics dict."""
        return {
            "max_alt_m": self.max_alt - self.ground_alt,
            "max_vel_ms": self.max_vel,
            "state": self.state_name,
        }
