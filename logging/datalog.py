"""
Binary flight data logger.

Writes fixed-size binary frames to SD card for maximum throughput.
At 25 Hz with 28-byte frames, that's 700 bytes/sec = ~2.4 MB/hour.
8 GB SD card = ~3,300 hours of recording. You'll run out of battery first.

Frame format (28 bytes, little-endian):
    u32  timestamp_ms
    u8   state
    f32  pressure_pa
    f32  temperature_c
    f32  alt_raw_m
    f32  alt_filtered_m
    f32  vel_filtered_ms
    u16  v_batt_mv
    u8   flags (bit0=armed, bit1=drogue_fired, bit2=main_fired, bit3=error)
"""

import struct
import os

FRAME_HEADER = b'\xAA\x55'  # sync bytes for frame alignment in decoder
FRAME_FORMAT = '<IB f f f f f H B'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)  # should be 28 bytes


class FlightLogger:
    """Writes binary telemetry frames to SD card."""

    def __init__(self, filename, flush_every=25):
        self.filename = filename
        self.flush_every = flush_every
        self._file = None
        self._count = 0
        self._total_frames = 0

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

        # Write file header: magic + version + frame size
        self._file.write(b'RKTLOG')    # 6 bytes magic
        self._file.write(struct.pack('<HH', 1, FRAME_SIZE))  # version, frame size
        self._file.flush()

        return fname

    def write_frame(self, timestamp_ms, state, pressure_pa, temperature_c,
                    alt_raw, alt_filtered, vel_filtered, v_batt_mv, flags):
        """Write one telemetry frame. Call at SAMPLE_RATE_HZ."""
        if self._file is None:
            return

        frame = struct.pack(
            FRAME_FORMAT,
            timestamp_ms,
            state,
            pressure_pa,
            temperature_c,
            alt_raw,
            alt_filtered,
            vel_filtered,
            v_batt_mv,
            flags,
        )
        self._file.write(FRAME_HEADER)
        self._file.write(frame)

        self._count += 1
        self._total_frames += 1

        if self._count >= self.flush_every:
            self._file.flush()
            self._count = 0

    def close(self):
        """Flush and close. Call on landing or shutdown."""
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None

    @property
    def frames_written(self):
        return self._total_frames

    def _file_exists(self, path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False
