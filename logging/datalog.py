"""
Binary flight data logger.

Writes fixed-size binary frames to SD card for maximum throughput.
At 25 Hz with 32-byte frames, that's 800 bytes/sec = ~2.7 MB/hour.
8 GB SD card = ~2,900 hours of recording. You'll run out of battery first.

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


class FlightLogger:
    """Writes binary telemetry frames to SD card."""

    def __init__(self, filename, flush_every=25):
        self.filename = filename
        self.flush_every = flush_every
        self._file = None
        self._count = 0
        self._total_frames = 0
        self._sd_failed = False

    def open(self):
        """Open log file. Creates new file with incrementing name if exists."""
        # Find next available filename
        base = self.filename.rsplit('.', 1)[0]
        ext = self.filename.rsplit('.', 1)[1] if '.' in self.filename else 'bin'

        fname = f"{base}.{ext}"
        idx = 1
        while self._file_exists(fname):
            fname = f"{base}_{idx:03d}.{ext}"
            idx += 1

        self._file = open(fname, 'wb')
        self._count = 0
        self._total_frames = 0
        self._sd_failed = False

        # Write file header: magic + version + frame size
        self._file.write(b'RKTLOG')    # 6 bytes magic
        self._file.write(struct.pack('<HH', 2, FRAME_SIZE))  # version 2, frame size
        self._file.flush()
        _try_sync()

        return fname

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
                _try_sync()
                self._count = 0
        except Exception as e:
            # SD card failed — stop writing to avoid crashing the sensor loop
            print(f"[SD] Write failed: {e}")
            self._sd_failed = True

    def notify_state_change(self, state):
        """Force immediate flush+sync on state transitions (captures critical moments)."""
        if self._file is None or self._sd_failed:
            return
        try:
            self._file.flush()
            _try_sync()
            self._count = 0
        except Exception as e:
            print(f"[SD] Sync on state change failed: {e}")
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
    def frames_written(self):
        return self._total_frames

    @property
    def sd_failed(self):
        return self._sd_failed

    def _file_exists(self, path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False
