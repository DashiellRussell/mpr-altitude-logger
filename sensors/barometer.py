"""
BMP180 barometer driver — I2C, MicroPython (GY-68 breakout).
Returns pressure (Pa) and temperature (°C).
"""

import struct
import time


class BMP180:
    """Minimal BMP180 driver tuned for high-rate avionics reads."""

    # Registers
    _REG_ID = 0xD0
    _REG_RESET = 0xE0
    _REG_CTRL_MEAS = 0xF4
    _REG_OUT_MSB = 0xF6
    _REG_CALIB = 0xAA

    # Measurement commands (written to _REG_CTRL_MEAS)
    _CMD_TEMP = 0x2E
    # Pressure commands by oversampling: 0=ultra low, 1=standard, 2=high, 3=ultra high
    _CMD_PRESS = (0x34, 0x74, 0xB4, 0xF4)

    # Oversampling setting (0-3). Higher = more accurate, slower.
    # 3 (ultra high) gives best altitude resolution, ~25.5ms per pressure read
    _OSS = 3

    def __init__(self, i2c, addr=0x77):
        self.i2c = i2c
        self.addr = addr
        self.oss = self._OSS

        chip_id = self._read_byte(self._REG_ID)
        if chip_id != 0x55:
            raise RuntimeError(f"Bad chip ID: 0x{chip_id:02x} (expected 0x55 for BMP180)")

        # Soft reset
        self._write_byte(self._REG_RESET, 0xB6)
        time.sleep_ms(10)

        # Read factory calibration
        self._read_calibration()

        # Pre-compute for pressure calc
        self._oss_shift = 8 - self.oss

    def _read_calibration(self):
        """Read 11 calibration coefficients from EEPROM."""
        raw = self.i2c.readfrom_mem(self.addr, self._REG_CALIB, 22)
        # BMP180 calibration is big-endian
        self.AC1 = struct.unpack_from('>h', raw, 0)[0]
        self.AC2 = struct.unpack_from('>h', raw, 2)[0]
        self.AC3 = struct.unpack_from('>h', raw, 4)[0]
        self.AC4 = struct.unpack_from('>H', raw, 6)[0]
        self.AC5 = struct.unpack_from('>H', raw, 8)[0]
        self.AC6 = struct.unpack_from('>H', raw, 10)[0]
        self.B1 = struct.unpack_from('>h', raw, 12)[0]
        self.B2 = struct.unpack_from('>h', raw, 14)[0]
        self.MB = struct.unpack_from('>h', raw, 16)[0]
        self.MC = struct.unpack_from('>h', raw, 18)[0]
        self.MD = struct.unpack_from('>h', raw, 20)[0]

        # Sanity check — all-zero or all-0xFF means bad EEPROM
        if self.AC1 == 0 or self.AC1 == -1:
            raise RuntimeError("BMP180 calibration data looks invalid")

    def _read_raw_temp(self):
        """Start temp measurement, wait, return raw value."""
        self._write_byte(self._REG_CTRL_MEAS, self._CMD_TEMP)
        time.sleep_ms(5)  # 4.5ms max conversion time
        raw = self.i2c.readfrom_mem(self.addr, self._REG_OUT_MSB, 2)
        return struct.unpack_from('>H', raw, 0)[0]

    def _read_raw_press(self):
        """Start pressure measurement, wait, return raw value."""
        self._write_byte(self._REG_CTRL_MEAS, self._CMD_PRESS[self.oss])
        # Wait time depends on oversampling: 5, 8, 14, 26 ms
        time.sleep_ms((2 + (3 << self.oss)))
        raw = self.i2c.readfrom_mem(self.addr, self._REG_OUT_MSB, 3)
        return ((raw[0] << 16) | (raw[1] << 8) | raw[2]) >> self._oss_shift

    def read(self):
        """Read compensated pressure (Pa) and temperature (°C).

        Returns:
            (pressure_pa: float, temperature_c: float)
        """
        # Temperature
        UT = self._read_raw_temp()
        X1 = (UT - self.AC6) * self.AC5 // 32768
        X2 = (self.MC * 2048) // (X1 + self.MD)
        B5 = X1 + X2
        temperature = (B5 + 8) / 160.0  # °C

        # Pressure
        UP = self._read_raw_press()
        B6 = B5 - 4000
        X1 = (self.B2 * (B6 * B6 // 4096)) // 2048
        X2 = self.AC2 * B6 // 2048
        X3 = X1 + X2
        B3 = (((self.AC1 * 4 + X3) << self.oss) + 2) // 4
        X1 = self.AC3 * B6 // 8192
        X2 = (self.B1 * (B6 * B6 // 4096)) // 65536
        X3 = (X1 + X2 + 2) // 4
        B4 = self.AC4 * (X3 + 32768) // 65536
        B7 = (UP - B3) * (50000 >> self.oss)

        if B7 < 0x80000000:
            pressure = (B7 * 2) // B4
        else:
            pressure = (B7 // B4) * 2

        X1 = (pressure // 256) * (pressure // 256)
        X1 = (X1 * 3038) // 65536
        X2 = (-7357 * pressure) // 65536
        pressure = pressure + (X1 + X2 + 3791) // 16

        return float(pressure), temperature

    def _read_byte(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

    def _write_byte(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))


def pressure_to_altitude(pressure_pa, sea_level_pa=101325.0):
    """Hypsometric formula: pressure → altitude (m) AGL.

    Args:
        pressure_pa: current pressure in Pascals
        sea_level_pa: ground-level reference pressure in Pascals

    Returns:
        Altitude in meters above ground level
    """
    if pressure_pa <= 0:
        return 0.0
    return 44330.0 * (1.0 - (pressure_pa / sea_level_pa) ** 0.1903)
