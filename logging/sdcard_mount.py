"""
SD card mount/unmount for MicroPython on Pico.
Uses SPI interface — the Pico doesn't have native SDIO.
"""

import os
from machine import Pin, SPI
import sdcard  # MicroPython built-in sdcard driver
import config


_sd = None
_vfs = None


def mount():
    """Mount SD card at /sd. Returns True on success."""
    global _sd, _vfs
    import time

    # Clear any stale mount (e.g. from interrupted main.py)
    try:
        os.umount("/sd")
    except OSError:
        pass

    try:
        cs = Pin(config.SPI_CS, Pin.OUT)
        cs.value(1)
        time.sleep_ms(100)  # Let CS settle after power-up

        spi = SPI(
            config.SPI_ID,
            baudrate=400_000,  # SD spec: init at ≤400 kHz
            polarity=0,
            phase=0,
            sck=Pin(config.SPI_SCK),
            mosi=Pin(config.SPI_MOSI),
            miso=Pin(config.SPI_MISO),
        )

        # Send 80 dummy clocks with CS high (SD card init sequence)
        spi.write(b'\xff' * 10)
        time.sleep_ms(10)

        _sd = sdcard.SDCard(spi, cs)

        # Ramp up to configured speed for data transfer
        spi.init(baudrate=config.SPI_BAUD)
        _vfs = os.VfsFat(_sd)
        os.mount(_vfs, "/sd")
        return True
    except Exception as e:
        print(f"[SD] Mount failed: {e}")
        return False


def unmount():
    """Safely unmount SD card."""
    global _sd, _vfs
    try:
        os.umount("/sd")
    except OSError:
        pass
    _sd = None
    _vfs = None


def is_mounted():
    """Check if SD is accessible."""
    try:
        os.listdir("/sd")
        return True
    except OSError:
        return False


def free_space_mb():
    """Return free space on SD in MB."""
    try:
        stat = os.statvfs("/sd")
        return (stat[0] * stat[3]) / (1024 * 1024)
    except OSError:
        return -1


def sync():
    """Force FAT metadata to disk. Falls back to no-op if unavailable."""
    try:
        os.sync()
    except AttributeError:
        pass
