"""
Binary flight data logger.

Writes fixed-size binary frames to SD card for maximum throughput.
At 50 Hz with 36-byte frames, that's 1800 bytes/sec = ~6.3 MB/hour.
8 GB SD card = ~1,200 hours of recording. You'll run out of battery first.

Each flight gets its own folder on the SD card:
    /sd/flight_001/
        flight.bin       — binary telemetry frames
        preflight.txt    — preflight check results and metadata
        boot.txt         — full boot serial output log
        crash.txt        — crash report (if previous session ended in WDT reset)

Frame format v3 (40 bytes, little-endian):
    u32  timestamp_ms
    u8   state
    f32  pressure_pa
    f32  temperature_c
    f32  alt_raw_m
    f32  alt_filtered_m
    f32  vel_filtered_ms
    u16  v_3v3_mv
    u16  v_5v_mv
    u16  v_9v_mv
    u8   flags (bit3=error, bits 0-2 reserved/legacy)
    --- v3 diagnostic fields ---
    u16  frame_us       (frame execution time in microseconds)
    u16  flush_us       (last flush duration in us, 0 on non-flush frames)
    u8   free_kb        (gc.mem_free() // 1024)
    u8   cpu_temp_c     (RP2040 internal temp, °C, offset +40 → 0=−40°C, 255=215°C)
    u8   i2c_errors     (cumulative I2C error count, wraps at 255)
    u8   overruns       (cumulative loop overrun count, wraps at 255)
"""

import struct
import os
import time as _time
from machine import mem32

FRAME_HEADER = b'\xAA\x55'  # sync bytes for frame alignment in decoder
FRAME_FORMAT = '<IBfffffHHHBHHBBBB'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)  # 40 bytes
LOG_VERSION = 3

# ── Watchdog scratch registers (survive WDT resets) ──────────
# RP2040 WATCHDOG base = 0x40058000, scratch regs at +0x0C..+0x28
# SCRATCH0 and SCRATCH4 may be used by MicroPython/bootrom — avoid them.
# We use only SCRATCH6 and SCRATCH7 (safe for application use).
_SCRATCH6 = 0x40058024  # magic (upper 16) | frame_count (lower 16)
_SCRATCH7 = 0x40058028  # timestamp_ms
_CRASH_MAGIC_HI = 0xDEAD


def write_scratch(frame_count, timestamp_ms, free_ram, flush_us, state, flags, cpu_temp):
    """Write crash context to scratch registers (survives WDT reset)."""
    mem32[_SCRATCH6] = (_CRASH_MAGIC_HI << 16) | (frame_count & 0xFFFF)
    mem32[_SCRATCH7] = timestamp_ms & 0xFFFFFFFF


def read_crash_report():
    """Read crash data from scratch registers. Returns dict or None."""
    val6 = mem32[_SCRATCH6]
    if (val6 >> 16) != _CRASH_MAGIC_HI:
        return None
    report = {
        'frame_count': val6 & 0xFFFF,
        'timestamp_ms': mem32[_SCRATCH7],
        'free_ram': 0,
        'flush_us': 0,
        'state': 0,
        'flags': 0,
        'cpu_temp': 0,
    }
    mem32[_SCRATCH6] = 0
    return report


_has_os_sync = hasattr(os, 'sync')


def _try_sync(wdt=None):
    """Force FAT metadata to disk if os.sync() is available."""
    if _has_os_sync:
        if wdt:
            wdt.feed()
        os.sync()
        if wdt:
            wdt.feed()


def _dir_exists(path):
    try:
        s = os.stat(path)
        return s[0] & 0x4000 != 0
    except OSError:
        return False


def _get_name_override():
    """Read flight name override from SD card (one-shot — deletes after reading)."""
    try:
        with open('/sd/_flight_name.txt', 'r') as f:
            name = f.read().strip()
        if name:
            try:
                os.remove('/sd/_flight_name.txt')
            except OSError:
                pass
            return name
    except OSError:
        pass
    return None


def _file_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def next_flight_dir():
    """Find the next available flight directory path (without creating it)."""
    override = _get_name_override()
    if override:
        override_dir = '/sd/' + override
        # If override dir already has flight.bin, fall through to auto-numbering
        if not _file_exists(override_dir + '/flight.bin'):
            return override_dir

    idx = 1
    while True:
        d = '/sd/flight_{:03d}'.format(idx)
        if not _dir_exists(d):
            return d
        idx += 1


def next_log_filename(base_path=None):
    """Predict the next flight log path. Returns e.g. '/sd/flight_001/flight.bin'."""
    return next_flight_dir() + '/flight.bin'


class FlightLogger:
    """Writes binary telemetry frames to a per-flight folder on SD card."""

    def __init__(self, filename=None, flush_every=25, sync_every=3, wdt=None):
        self.flush_every = flush_every
        self.sync_every = sync_every  # os.sync() every N flushes
        self._wdt = wdt  # watchdog to feed during slow SD syncs
        self._file = None
        self._count = 0
        self._flush_count = 0
        self._total_frames = 0
        self._sd_failed = False
        self._flight_dir = None
        self._last_flush_us = 0  # duration of most recent flush (logged in next frame)
        # Pre-allocated write buffer: sync header + frame data (zero allocation in hot loop)
        self._write_buf = bytearray(2 + FRAME_SIZE)
        self._write_buf[0] = 0xAA
        self._write_buf[1] = 0x55

    def open(self):
        """Create flight folder and open flight.bin inside it."""
        flight_dir = next_flight_dir()

        # Create directory (ok if it already exists, but verify it was created)
        try:
            os.mkdir(flight_dir)
        except OSError:
            if not _dir_exists(flight_dir):
                raise

        self._flight_dir = flight_dir
        fname = flight_dir + '/flight.bin'

        if not _has_os_sync:
            print('[SD] WARNING: os.sync() unavailable — data at risk if power lost')

        self._file = open(fname, 'wb')
        self._count = 0
        self._flush_count = 0
        self._total_frames = 0
        self._sd_failed = False

        # Write file header: magic + version + frame size
        self._file.write(b'RKTLOG')    # 6 bytes magic
        self._file.write(struct.pack('<HH', LOG_VERSION, FRAME_SIZE))
        self._file.flush()
        _try_sync(self._wdt)

        return fname

    def write_preflight(self, content):
        """Write preflight check results to the flight folder."""
        if self._flight_dir is None:
            return
        try:
            with open(self._flight_dir + '/preflight.txt', 'w') as f:
                f.write(content)
            _try_sync(self._wdt)
        except Exception as e:
            print('[SD] Preflight write failed: {}'.format(e))

    def write_boot_log(self, lines):
        """Write captured boot serial output to the flight folder."""
        if self._flight_dir is None:
            return
        try:
            with open(self._flight_dir + '/boot.txt', 'w') as f:
                for line in lines:
                    f.write(line)
                    f.write('\n')
            _try_sync(self._wdt)
        except Exception as e:
            print('[SD] Boot log write failed: {}'.format(e))

    def write_crash_report(self, crash_data, reboot_reason):
        """Write crash report from previous session's scratch registers."""
        if self._flight_dir is None:
            return
        try:
            state_names = {0: 'PAD', 1: 'BOOST', 2: 'COAST', 3: 'APOGEE',
                           4: 'DROGUE', 5: 'MAIN', 6: 'LANDED'}
            with open(self._flight_dir + '/crash.txt', 'w') as f:
                f.write('CRASH REPORT — Previous session ended in WDT reset\n')
                f.write('=' * 50 + '\n')
                f.write('Reboot reason: {}\n'.format(reboot_reason))
                f.write('Last frame: #{}\n'.format(crash_data['frame_count']))
                f.write('Last timestamp: {} ms\n'.format(crash_data['timestamp_ms']))
                f.write('Free RAM: {} bytes ({} KB)\n'.format(
                    crash_data['free_ram'], crash_data['free_ram'] // 1024))
                f.write('Last flush: {} us\n'.format(crash_data['flush_us']))
                f.write('State: {} ({})\n'.format(
                    state_names.get(crash_data['state'], '?'), crash_data['state']))
                f.write('Flags: 0x{:02X}\n'.format(crash_data['flags']))
                f.write('CPU temp: {} C\n'.format(crash_data['cpu_temp'] - 40))
            _try_sync(self._wdt)
        except Exception as e:
            print('[SD] Crash report write failed: {}'.format(e))

    def write_frame(self, timestamp_ms, state, pressure_pa, temperature_c,
                    alt_raw, alt_filtered, vel_filtered,
                    v_3v3_mv, v_5v_mv, v_9v_mv, flags,
                    frame_us=0, free_kb=0, cpu_temp_c=0,
                    i2c_errors=0, overruns=0):
        """Write one telemetry frame. Call at SAMPLE_RATE_HZ."""
        if self._file is None or self._sd_failed:
            return

        try:
            # Pack into pre-allocated buffer (zero allocation)
            struct.pack_into(
                FRAME_FORMAT, self._write_buf, 2,
                timestamp_ms, state,
                pressure_pa, temperature_c,
                alt_raw, alt_filtered, vel_filtered,
                v_3v3_mv, v_5v_mv, v_9v_mv, flags,
                frame_us, self._last_flush_us,
                free_kb, cpu_temp_c,
                i2c_errors, overruns,
            )
            self._file.write(self._write_buf)

            self._count += 1
            self._total_frames += 1

            if self._count >= self.flush_every:
                # Write crash context to scratch regs before flush (survives WDT reset)
                write_scratch(self._total_frames, timestamp_ms,
                              free_kb * 1024, self._last_flush_us,
                              state, flags, cpu_temp_c)
                if self._wdt:
                    self._wdt.feed()
                _flush_start = _time.ticks_us()
                self._file.flush()
                self._last_flush_us = min(_time.ticks_diff(_time.ticks_us(), _flush_start), 65535)
                if self._wdt:
                    self._wdt.feed()
                self._flush_count += 1
                if self._flush_count >= self.sync_every:
                    _try_sync(self._wdt)
                    self._flush_count = 0
                self._count = 0
        except OSError as e:
            # SD write failed — retry once before giving up
            try:
                self._file.write(self._write_buf)
                self._total_frames += 1
            except OSError:
                print(f"[SD] Write failed at frame #{self._total_frames}: {e}")
                self._sd_failed = True

    def notify_state_change(self, state):
        """Force immediate flush+sync on state transitions (captures critical moments)."""
        if self._file is None or self._sd_failed:
            return
        try:
            self._file.flush()
            _try_sync(self._wdt)  # Always sync on state change — these are critical
            self._count = 0
            self._flush_count = 0
        except Exception as e:
            print(f"[SD] Sync on state change failed at frame #{self._total_frames}: {e}")
            self._sd_failed = True

    def try_recover(self):
        """Attempt to recover from SD failure — close broken file, open a new one.

        Call periodically (e.g. every few seconds) from main loop when sd_failed is True.
        Returns True if recovery succeeded and logging has resumed.
        """
        if not self._sd_failed:
            return True

        # Close the broken file handle
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

        # Try to open a new file in a new flight folder
        try:
            flight_dir = next_flight_dir()
            try:
                os.mkdir(flight_dir)
            except OSError:
                if not _dir_exists(flight_dir):
                    return False

            self._flight_dir = flight_dir
            fname = flight_dir + '/flight.bin'
            self._file = open(fname, 'wb')
            self._count = 0
            self._flush_count = 0
            self._sd_failed = False
            self._last_flush_us = 0

            # Write file header
            self._file.write(b'RKTLOG')
            self._file.write(struct.pack('<HH', LOG_VERSION, FRAME_SIZE))
            self._file.flush()
            _try_sync(self._wdt)

            print(f"[SD] Recovered — logging to {fname}")
            return True
        except Exception as e:
            print(f"[SD] Recovery failed: {e}")
            return False

    def close(self):
        """Flush and close. Call on landing or shutdown."""
        if self._file:
            try:
                self._file.flush()
                _try_sync(self._wdt)
                self._file.close()
            except Exception:
                pass
            self._file = None

    @property
    def flight_dir(self):
        return self._flight_dir

    @property
    def frames_written(self):
        return self._total_frames

    @property
    def sd_failed(self):
        return self._sd_failed
