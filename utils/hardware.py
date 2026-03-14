"""
Hardware output control — LED status patterns.

Two implementations:
  StatusLED  — manual tick(), used by tests that need explicit control
  TimerLED   — hardware Timer drives the pattern (no _thread, no GIL contention)
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


class TimerLED:
    """LED pattern driver using hardware Timer — no _thread, no GIL contention.

    The Timer callback runs as a soft IRQ on the same core, between bytecodes.
    This avoids cross-core GIL thrashing that _thread causes on RP2040.
    """

    def __init__(self, timer_id=-1, tick_ms=25):
        from machine import Timer
        self.led = Pin(config.LED_PIN, Pin.OUT)
        self._pattern = None
        self._idx = 0
        self._last = time.ticks_ms()
        self._timer = Timer(timer_id)  # -1 = virtual timer (only option on RP2040)
        self._timer.init(period=tick_ms, mode=Timer.PERIODIC, callback=self._cb)

    def on(self):
        """Solid on (error mode)."""
        self._pattern = None
        self.led.value(1)

    def off(self):
        self._pattern = None
        self.led.value(0)

    def set_pattern(self, pattern_ms):
        """Set blink pattern: list of on/off durations in ms."""
        self._idx = 0
        self._last = time.ticks_ms()
        self.led.value(1)
        self._pattern = pattern_ms

    def stop(self):
        """Stop the timer and turn off LED."""
        self._timer.deinit()
        self.led.value(0)

    def _cb(self, t):
        """Timer callback — runs as soft IRQ, must not allocate."""
        p = self._pattern
        if p is None:
            return
        now = time.ticks_ms()
        idx = self._idx
        if time.ticks_diff(now, self._last) >= p[idx]:
            self._idx = (idx + 1) % len(p)
            self.led.toggle()
            self._last = now


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
