"""
Mock MicroPython modules for CPython test environment.

MicroPython-specific modules (machine, _thread, sdcard, etc.) don't exist
on CPython. This conftest patches them before any avionics code is imported.

NOTE: The project has a `logging/` package that shadows stdlib `logging`.
We handle this by ensuring stdlib logging is importable before the project
root gets added to sys.path.
"""

import sys
import os
import types
from unittest.mock import MagicMock

# Ensure stdlib logging is cached before project path shadows it
import logging as _stdlib_logging  # noqa: F401

# Add project root to path for imports
_project_root = os.path.dirname(os.path.dirname(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Patch time module with MicroPython-specific functions
import time
if not hasattr(time, 'ticks_ms'):
    time.ticks_ms = lambda: int(time.time() * 1000)
if not hasattr(time, 'ticks_us'):
    time.ticks_us = lambda: int(time.time() * 1000000)
if not hasattr(time, 'ticks_diff'):
    time.ticks_diff = lambda a, b: a - b
if not hasattr(time, 'sleep_ms'):
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)


def _setup_micropython_mocks():
    """Install mock modules that stand in for MicroPython builtins."""

    # machine module — Pin, SPI, SoftI2C, ADC, freq, WDT
    machine = types.ModuleType('machine')
    machine.Pin = MagicMock()
    machine.SPI = MagicMock()
    machine.SoftI2C = MagicMock()
    machine.ADC = MagicMock()
    machine.freq = MagicMock(return_value=200_000_000)
    machine.WDT = MagicMock()
    sys.modules['machine'] = machine

    # _thread module
    _thread = types.ModuleType('_thread')
    _thread.start_new_thread = MagicMock()
    sys.modules['_thread'] = _thread

    # sdcard module
    sdcard = types.ModuleType('sdcard')
    sdcard.SDCard = MagicMock()
    sys.modules['sdcard'] = sdcard


# Run mocks before anything imports avionics code
_setup_micropython_mocks()
