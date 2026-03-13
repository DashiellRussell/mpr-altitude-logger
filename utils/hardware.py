"""
Hardware output control — LED status patterns.
"""

import time
from machine import Pin
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
    6: [100, 100, 100, 100, 100, 800],  # LANDED: triple flash (solid = error only)
}
