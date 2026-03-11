"""
Flight state machine — automatic phase detection and deployment logic.

States:
    PAD     → sitting on the pad, recording ground reference
    BOOST   → motor burning, rapid altitude gain
    COAST   → motor burnout, still ascending
    APOGEE  → peak altitude detected
    DROGUE  → drogue deployed, descending fast
    MAIN    → main chute deployed (altitude trigger)
    LANDED  → on the ground, buzzer active for recovery

Transitions are based on Kalman-filtered altitude and velocity.
No accelerometer needed — the Kalman velocity estimate is enough.
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
    All deployment decisions go through here.
    """

    def __init__(self):
        self.state = PAD
        self.armed = False

        # Tracking
        self.ground_alt = 0.0
        self.max_alt = 0.0
        self.max_vel = 0.0
        self.launch_time = 0
        self.apogee_time = 0

        # Detection counters
        self._apogee_count = 0
        self._landed_start = 0
        self._launch_alt_start = 0.0
        self._launch_time_start = 0

        # Deployment flags
        self.drogue_fired = False
        self.main_fired = False

    def set_ground_reference(self, alt):
        """Set ground-level altitude (call after averaging on pad)."""
        self.ground_alt = alt

    def set_armed(self, armed):
        """Arm/disarm deployment. Physical switch should gate this."""
        self.armed = armed

    def update(self, alt, vel, now_ms):
        """
        Run state machine with latest filtered data.
        
        Args:
            alt: Kalman-filtered altitude AGL (m)
            vel: Kalman-filtered vertical velocity (m/s, positive=up)
            now_ms: current time in ms (time.ticks_ms)
            
        Returns:
            (state, deploy_drogue, deploy_main)
            deploy flags are True only on the transition tick
        """
        deploy_drogue = False
        deploy_main = False

        # Track maxima
        if alt > self.max_alt:
            self.max_alt = alt
        if vel > self.max_vel:
            self.max_vel = vel

        agl = alt - self.ground_alt

        if self.state == PAD:
            # Detect launch: sustained altitude gain
            if agl > config.LAUNCH_ACCEL_THRESHOLD:
                if self._launch_time_start == 0:
                    self._launch_time_start = now_ms
                elif time.ticks_diff(now_ms, self._launch_time_start) > config.LAUNCH_DETECT_WINDOW * 1000:
                    self.state = BOOST
                    self.launch_time = now_ms
            else:
                self._launch_time_start = 0

        elif self.state == BOOST:
            # Detect burnout: velocity drops significantly from peak
            if self.max_vel > 0 and vel < self.max_vel - config.COAST_VEL_THRESHOLD:
                self.state = COAST

        elif self.state == COAST:
            # Detect apogee: velocity near zero or negative
            if vel < config.APOGEE_VEL_THRESHOLD:
                self._apogee_count += 1
                if self._apogee_count >= config.APOGEE_CONFIRM_COUNT:
                    self.state = APOGEE
                    self.apogee_time = now_ms
                    # Fire drogue
                    if self.armed and not self.drogue_fired:
                        deploy_drogue = True
                        self.drogue_fired = True
            else:
                self._apogee_count = 0

        elif self.state == APOGEE:
            # Transition to drogue descent immediately
            self.state = DROGUE

        elif self.state == DROGUE:
            # Detect main deployment altitude
            if agl <= config.MAIN_DEPLOY_ALT and vel < 0:
                self.state = MAIN
                if self.armed and not self.main_fired:
                    deploy_main = True
                    self.main_fired = True

        elif self.state == MAIN:
            # Detect landing: near-zero velocity for sustained period
            if abs(vel) < config.LANDED_VEL_THRESHOLD:
                if self._landed_start == 0:
                    self._landed_start = now_ms
                elif time.ticks_diff(now_ms, self._landed_start) > config.LANDED_CONFIRM_SECONDS * 1000:
                    self.state = LANDED
            else:
                self._landed_start = 0

        elif self.state == LANDED:
            pass  # Terminal state — buzzer handled externally

        return self.state, deploy_drogue, deploy_main

    @property
    def state_name(self):
        return STATE_NAMES[self.state]

    def get_stats(self):
        """Return flight statistics dict."""
        return {
            "max_alt_m": self.max_alt - self.ground_alt,
            "max_vel_ms": self.max_vel,
            "state": self.state_name,
            "drogue_fired": self.drogue_fired,
            "main_fired": self.main_fired,
        }
