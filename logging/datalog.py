"""
Binary flight data logger.

Writes fixed-size binary frames to SD card for maximum throughput.
At 25 Hz with 32-byte frames, that's 800 bytes/sec = ~2.7 MB/hour.
8 GB SD card = ~2,900 hours of recording. You'll run out of battery first.

Each flight gets its own folder on the SD card:
    /sd/flight_001/
        flight.bin       — binary telemetry frames
        preflight.txt    — preflight check results and metadata

Frame format (32 bytes, little-endian):
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
"""

import struct
import os

FRAME_HEADER = b'\xAA\x55'  # sync bytes for frame alignment in decoder
FRAME_FORMAT = '<IBfffffHHHB'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)  # 32 bytes


def _try_sync():
    """Force FAT metadata to disk if os.sync() is available."""
    try:
        os.sync()
    except AttributeError:
        pass  # MicroPython build without os.sync() — flush-only fallback


def _dir_exists(path):
    try:
        s = os.stat(path)
        return s[0] & 0x4000 != 0
    except OSError:
        return False


def _get_name_override():
    """Read flight name override from SD card, or return None."""
    try:
        with open('/sd/_flight_name.txt', 'r') as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    return None


def next_flight_dir():
    """Find the next available flight directory path (without creating it)."""
    override = _get_name_override()
    if override:
        return '/sd/' + override

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

    def __init__(self, filename=None, flush_every=25, sync_every=10):
        self.flush_every = flush_every
        self.sync_every = sync_every  # os.sync() every N flushes (not every flush)
        self._file = None
        self._count = 0
        self._flush_count = 0
        self._total_frames = 0
        self._sd_failed = False
        self._flight_dir = None

    def open(self):
        """Create flight folder and open flight.bin inside it."""
        flight_dir = next_flight_dir()

        # Create directory (ok if override folder already exists)
        try:
            os.mkdir(flight_dir)
        except OSError:
            pass

        self._flight_dir = flight_dir
        fname = flight_dir + '/flight.bin'

        self._file = open(fname, 'wb')
        self._count = 0
        self._flush_count = 0
        self._total_frames = 0
        self._sd_failed = False

        # Write file header: magic + version + frame size
        self._file.write(b'RKTLOG')    # 6 bytes magic
        self._file.write(struct.pack('<HH', 2, FRAME_SIZE))  # version 2, frame size
        self._file.flush()
        _try_sync()

        return fname

    def write_preflight(self, content):
        """Write preflight check results to the flight folder."""
        if self._flight_dir is None:
            return
        try:
            with open(self._flight_dir + '/preflight.txt', 'w') as f:
                f.write(content)
            _try_sync()
        except Exception as e:
            print('[SD] Preflight write failed: {}'.format(e))

    def write_frame(self, timestamp_ms, state, pressure_pa, temperature_c,
                    alt_raw, alt_filtered, vel_filtered,
                    v_3v3_mv, v_5v_mv, v_9v_mv, flags):
        """Write one telemetry frame. Call at SAMPLE_RATE_HZ."""
        if self._file is None or self._sd_failed:
            return

        try:
            frame = struct.pack(
                FRAME_FORMAT,
                timestamp_ms,
                state,
                pressure_pa,
                temperature_c,
                alt_raw,
                alt_filtered,
                vel_filtered,
                v_3v3_mv,
                v_5v_mv,
                v_9v_mv,
                flags,
            )
            self._file.write(FRAME_HEADER)
            self._file.write(frame)

            self._count += 1
            self._total_frames += 1

            if self._count >= self.flush_every:
                self._file.flush()
                self._flush_count += 1
                # os.sync() is expensive — only do it periodically, not every flush
                if self._flush_count >= self.sync_every:
                    _try_sync()
                    self._flush_count = 0
                self._count = 0
        except Exception as e:
            # SD card failed — log details for diagnosis then stop writing
            print(f"[SD] Write failed at frame #{self._total_frames}: {e}")
            self._sd_failed = True

    def notify_state_change(self, state):
        """Force immediate flush+sync on state transitions (captures critical moments)."""
        if self._file is None or self._sd_failed:
            return
        try:
            self._file.flush()
            _try_sync()  # Always sync on state change — these are critical
            self._count = 0
            self._flush_count = 0
        except Exception as e:
            print(f"[SD] Sync on state change failed at frame #{self._total_frames}: {e}")
            self._sd_failed = True

    def close(self):
        """Flush and close. Call on landing or shutdown."""
        if self._file:
            try:
                self._file.flush()
                _try_sync()
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
