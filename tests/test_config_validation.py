"""Tests for config validation."""

import config
import pytest


class TestConfigDefaults:
    """Default config values should pass validation."""

    def test_default_config_passes(self):
        config.validate()  # should not raise


class TestConfigValidation:
    """Validation should catch bad values."""

    def test_negative_q_alt(self):
        orig = config.KALMAN_Q_ALT
        try:
            config.KALMAN_Q_ALT = -1.0
            with pytest.raises(ValueError, match="KALMAN_Q_ALT"):
                config.validate()
        finally:
            config.KALMAN_Q_ALT = orig

    def test_negative_q_vel(self):
        orig = config.KALMAN_Q_VEL
        try:
            config.KALMAN_Q_VEL = -0.5
            with pytest.raises(ValueError, match="KALMAN_Q_VEL"):
                config.validate()
        finally:
            config.KALMAN_Q_VEL = orig

    def test_negative_r_alt(self):
        orig = config.KALMAN_R_ALT
        try:
            config.KALMAN_R_ALT = 0
            with pytest.raises(ValueError, match="KALMAN_R_ALT"):
                config.validate()
        finally:
            config.KALMAN_R_ALT = orig

    def test_sample_rate_too_high(self):
        orig = config.SAMPLE_RATE_HZ
        try:
            config.SAMPLE_RATE_HZ = 200
            with pytest.raises(ValueError, match="SAMPLE_RATE_HZ"):
                config.validate()
        finally:
            config.SAMPLE_RATE_HZ = orig

    def test_sample_rate_zero(self):
        orig = config.SAMPLE_RATE_HZ
        try:
            config.SAMPLE_RATE_HZ = 0
            with pytest.raises(ValueError, match="SAMPLE_RATE_HZ"):
                config.validate()
        finally:
            config.SAMPLE_RATE_HZ = orig

    def test_pin_conflict_detected(self):
        orig = config.LED_PIN
        try:
            config.LED_PIN = config.I2C_SDA  # conflict!
            with pytest.raises(ValueError, match="Pin conflict"):
                config.validate()
        finally:
            config.LED_PIN = orig
