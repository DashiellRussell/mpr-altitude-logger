"""
Power rail monitoring — reads 3.3V, 5V, and 9V regulator voltages via ADC pins.
3.3V is direct, 5V and 9V through voltage dividers.
"""

from machine import ADC, Pin
import config


class PowerMonitor:
    """Reads voltage rails through resistor dividers on ADC pins."""

    def __init__(self):
        self.adc_3v = ADC(Pin(config.ADC_V3))
        self.adc_5v = ADC(Pin(config.ADC_V5))
        self.adc_9v = ADC(Pin(config.ADC_V9))

    def _read_mv(self, adc, divider_ratio):
        """Read ADC and convert to actual voltage in mV."""
        raw = adc.read_u16()
        # Flag anomalous readings (stuck low/high)
        if raw <= 100 or raw >= 65400:
            return 0
        v_adc = (raw / config.ADC_RESOLUTION) * config.VREF
        return int(v_adc * divider_ratio * 1000)

    def read_3v3_mv(self):
        return self._read_mv(self.adc_3v, config.VDIV_3V)

    def read_5v_mv(self):
        return self._read_mv(self.adc_5v, config.VDIV_5V)

    def read_9v_mv(self):
        return self._read_mv(self.adc_9v, config.VDIV_9V)

    def read_battery_mv(self):
        """Battery voltage = 9V rail (pre-regulator)."""
        return self.read_9v_mv()

    def read_all(self):
        """Returns (v3v3_mv, v5_mv, v9_mv)."""
        return (
            self.read_3v3_mv(),
            self.read_5v_mv(),
            self.read_9v_mv(),
        )

    def check_health(self):
        """Quick sanity check on rails. Returns list of warning strings."""
        warnings = []
        v3, v5, v9 = self.read_all()

        if v3 < 3000 or v3 > 3600:
            warnings.append(f"3.3V RAIL OUT OF SPEC: {v3}mV")
        if v5 < 4500 or v5 > 5500:
            warnings.append(f"5V RAIL OUT OF SPEC: {v5}mV")
        if v9 < 8000 or v9 > 10000:
            warnings.append(f"9V RAIL OUT OF SPEC: {v9}mV")

        return warnings
