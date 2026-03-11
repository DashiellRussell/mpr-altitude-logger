"""
Power rail monitoring — reads 5V and 9V regulator voltages via ADC pins
through voltage dividers.
"""

from machine import ADC, Pin
import config


class PowerMonitor:
    """Reads voltage rails through resistor dividers on ADC pins."""

    def __init__(self):
        self.adc_5v = ADC(Pin(config.ADC_V5))
        self.adc_9v = ADC(Pin(config.ADC_V9))

    def _read_mv(self, adc, divider_ratio):
        """Read ADC and convert to actual voltage in mV."""
        raw = adc.read_u16()
        v_adc = (raw / config.ADC_RESOLUTION) * config.VREF
        return int(v_adc * divider_ratio * 1000)

    def read_5v_mv(self):
        return self._read_mv(self.adc_5v, config.VDIV_5V)

    def read_9v_mv(self):
        return self._read_mv(self.adc_9v, config.VDIV_9V)

    def read_all(self):
        """Returns (v5_mv, v9_mv)."""
        return (
            self.read_5v_mv(),
            self.read_9v_mv(),
        )

    def check_health(self):
        """Quick sanity check on rails. Returns list of warning strings."""
        warnings = []
        v5, v9 = self.read_all()

        if v5 < 4500 or v5 > 5500:
            warnings.append(f"5V RAIL OUT OF SPEC: {v5}mV")
        if v9 < 8000 or v9 > 10000:
            warnings.append(f"9V RAIL OUT OF SPEC: {v9}mV")

        return warnings
