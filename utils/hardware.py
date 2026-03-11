"""
Hardware output control — LED status, buzzer, deployment charges.
"""

import time
from machine import Pin, PWM
import config


class StatusLED:
    """Onboard LED with pattern support."""

    def __init__(self):
        self.led = Pin(config.LED_PIN, Pin.OUT)
        self._pattern = None
        self._pattern_idx = 0
        self._last_toggle = 0

    def on(self):
        self.led.value(1)
        self._pattern = None

    def off(self):
        self.led.value(0)
        self._pattern = None

    def set_pattern(self, pattern_ms):
        """Set blink pattern: list of on/off durations in ms.
        e.g. [100, 100] = fast blink, [500, 500] = slow blink,
        [100, 100, 100, 500] = double-flash
        """
        self._pattern = pattern_ms
        self._pattern_idx = 0
        self._last_toggle = time.ticks_ms()
        self.led.value(1)

    def tick(self, now_ms):
        """Call regularly to advance blink pattern."""
        if self._pattern is None:
            return
        if time.ticks_diff(now_ms, self._last_toggle) >= self._pattern[self._pattern_idx]:
            self._pattern_idx = (self._pattern_idx + 1) % len(self._pattern)
            self.led.toggle()
            self._last_toggle = now_ms


# LED patterns for each flight state
LED_PATTERNS = {
    0: [1000, 1000],           # PAD: slow blink
    1: [50, 50],               # BOOST: fast blink
    2: [100, 100],             # COAST: medium blink
    3: [100, 100, 100, 500],   # APOGEE: double flash
    4: [200, 200],             # DROGUE: medium-fast
    5: [300, 300],             # MAIN: medium
    6: None,                   # LANDED: solid on
}


class Buzzer:
    """Piezo buzzer for recovery beacon and status tones."""

    def __init__(self):
        self.pwm = PWM(Pin(config.BUZZER_PIN))
        self.pwm.duty_u16(0)
        self._beeping = False
        self._beep_end = 0

    def beep(self, freq=2700, duration_ms=100):
        """Short beep."""
        self.pwm.freq(freq)
        self.pwm.duty_u16(32768)  # 50% duty
        self._beep_end = time.ticks_ms() + duration_ms
        self._beeping = True

    def recovery_beacon(self, now_ms):
        """Loud intermittent beeping for recovery. Call in LANDED state."""
        # 500ms on, 500ms off cycle
        phase = (now_ms // 500) % 2
        if phase == 0:
            self.pwm.freq(2700)
            self.pwm.duty_u16(32768)
        else:
            self.pwm.duty_u16(0)

    def off(self):
        self.pwm.duty_u16(0)
        self._beeping = False

    def tick(self, now_ms):
        if self._beeping and time.ticks_diff(now_ms, self._beep_end) > 0:
            self.off()


class DeployChannel:
    """E-match deployment output with safety interlocks."""

    def __init__(self):
        self.pin = Pin(config.DEPLOY_PIN, Pin.OUT, value=0)
        self._fire_end = 0
        self._firing = False

    def fire(self):
        """Fire deployment charge for configured pulse duration.
        
        SAFETY: Only call this through the state machine with ARM check.
        """
        self.pin.value(1)
        self._fire_end = time.ticks_ms() + config.DEPLOY_PULSE_MS
        self._firing = True

    def tick(self, now_ms):
        """Auto-shutoff after pulse duration."""
        if self._firing and time.ticks_diff(now_ms, self._fire_end) > 0:
            self.pin.value(0)
            self._firing = False

    def safe(self):
        """Force pin low immediately."""
        self.pin.value(0)
        self._firing = False


class ArmSwitch:
    """Physical arm switch — active LOW with internal pull-up."""

    def __init__(self):
        self.pin = Pin(config.ARM_PIN, Pin.IN, Pin.PULL_UP)

    @property
    def armed(self):
        return self.pin.value() == 0  # active LOW
