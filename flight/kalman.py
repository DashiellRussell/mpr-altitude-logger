"""
1D Kalman filter for altitude and vertical velocity estimation.

State vector: [altitude, velocity]
Measurement: barometric altitude only

This is the core of what makes this a flight computer rather than a datalogger.
The filter smooths noisy barometer readings and gives you a velocity estimate
you'd otherwise need an accelerometer for.
"""

import config


class AltitudeKalman:
    """
    Constant-velocity Kalman filter.
    
    State: x = [altitude (m), velocity (m/s)]
    Measurement: z = barometric altitude (m)
    
    The trick: even with just a barometer, differencing + filtering gives
    surprisingly good velocity estimates, which is what you need for apogee
    detection.
    """

    def __init__(self):
        # State vector [alt, vel]
        self.x_alt = 0.0
        self.x_vel = 0.0

        # Covariance matrix (2x2, stored as 4 elements)
        # P = [[p00, p01], [p10, p11]]
        self.p00 = 100.0
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = 100.0

        # Process noise
        self.q_alt = config.KALMAN_Q_ALT
        self.q_vel = config.KALMAN_Q_VEL

        # Measurement noise
        self.r = config.KALMAN_R_ALT

        self._initialized = False

    def reset(self, initial_alt=0.0):
        """Reset filter with known altitude."""
        self.x_alt = initial_alt
        self.x_vel = 0.0
        self.p00 = 100.0
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = 100.0
        self._initialized = True

    def update(self, measured_alt, dt):
        """
        Run one predict-update cycle.
        
        Args:
            measured_alt: barometric altitude in meters (AGL)
            dt: time since last update in seconds
            
        Returns:
            (filtered_alt, filtered_vel)
        """
        if not self._initialized:
            self.reset(measured_alt)
            return self.x_alt, self.x_vel

        if dt <= 0:
            return self.x_alt, self.x_vel

        # ── PREDICT ─────────────────────────────
        # x_pred = F @ x
        # F = [[1, dt], [0, 1]]
        pred_alt = self.x_alt + self.x_vel * dt
        pred_vel = self.x_vel

        # P_pred = F @ P @ F^T + Q
        pp00 = self.p00 + dt * (self.p10 + self.p01) + dt * dt * self.p11 + self.q_alt
        pp01 = self.p01 + dt * self.p11
        pp10 = self.p10 + dt * self.p11
        pp11 = self.p11 + self.q_vel

        # ── UPDATE ──────────────────────────────
        # H = [1, 0] — we only measure altitude
        # y = z - H @ x_pred
        innovation = measured_alt - pred_alt

        # S = H @ P_pred @ H^T + R
        s = pp00 + self.r

        # K = P_pred @ H^T / S
        if abs(s) < 1e-10:
            return pred_alt, pred_vel

        k0 = pp00 / s
        k1 = pp10 / s

        # x = x_pred + K * y
        self.x_alt = pred_alt + k0 * innovation
        self.x_vel = pred_vel + k1 * innovation

        # P = (I - K @ H) @ P_pred
        self.p00 = (1.0 - k0) * pp00
        self.p01 = (1.0 - k0) * pp01
        self.p10 = pp10 - k1 * pp00
        self.p11 = pp11 - k1 * pp01

        return self.x_alt, self.x_vel

    @property
    def altitude(self):
        return self.x_alt

    @property
    def velocity(self):
        return self.x_vel
