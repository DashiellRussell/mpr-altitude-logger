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
    try:
        spi = SPI(
            config.SPI_ID,
            baudrate=config.SPI_BAUD,
            polarity=0,
            phase=0,
            sck=Pin(config.SPI_SCK),
            mosi=Pin(config.SPI_MOSI),
            miso=Pin(config.SPI_MISO),
        )
        cs = Pin(config.SPI_CS, Pin.OUT, value=1)
        _sd = sdcard.SDCard(spi, cs)
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
    except:
        pass
    _sd = None
    _vfs = None


def is_mounted():
    """Check if SD is accessible."""
    try:
        os.listdir("/sd")
        return True
    except:
        return False


def free_space_mb():
    """Return free space on SD in MB."""
    try:
        stat = os.statvfs("/sd")
        return (stat[0] * stat[3]) / (1024 * 1024)
    except:
        return -1
