"""Tests for the flight data logger."""

import os
import struct
import sys
import tempfile
import importlib

# The project's `logging/` package shadows stdlib `logging`.
# Import it explicitly using importlib with the project path.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Temporarily ensure project root is first in path for this import
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Force-load our project's logging.datalog (may conflict with stdlib)
_spec = importlib.util.spec_from_file_location(
    'proj_datalog',
    os.path.join(_project_root, 'logging', 'datalog.py'),
)
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)

FlightLogger = dl.FlightLogger
FRAME_FORMAT = dl.FRAME_FORMAT
FRAME_SIZE = dl.FRAME_SIZE
FRAME_HEADER = dl.FRAME_HEADER
_dir_exists = dl._dir_exists
_file_exists = dl._file_exists


class TestFramePacking:
    """Verify the pre-allocated buffer packing matches struct.pack."""

    def test_pack_into_matches_pack(self):
        """pack_into to write_buf should produce identical bytes to pack."""
        args = (12345, 1, 101325.0, 22.5, 100.0, 99.8, 12.3, 3300, 5000, 9000, 0)

        # pack (old method)
        expected = FRAME_HEADER + struct.pack(FRAME_FORMAT, *args)

        # pack_into (new method)
        buf = bytearray(2 + FRAME_SIZE)
        buf[0] = 0xAA
        buf[1] = 0x55
        struct.pack_into(FRAME_FORMAT, buf, 2, *args)

        assert bytes(buf) == expected

    def test_frame_size_correct(self):
        assert FRAME_SIZE == 32  # documented as 32 bytes

    def test_error_flags_encoding(self):
        """Flags byte should correctly encode error bit."""
        buf = bytearray(2 + FRAME_SIZE)
        buf[0] = 0xAA
        buf[1] = 0x55
        struct.pack_into(FRAME_FORMAT, buf, 2,
                         0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0x08)

        # Last byte should be 0x08
        assert buf[-1] == 0x08


class TestMkdirErrorHandling:
    """Only swallow OSError when dir already exists."""

    def test_dir_exists_true_for_existing(self):
        with tempfile.TemporaryDirectory() as d:
            assert _dir_exists(d) is True

    def test_dir_exists_false_for_missing(self):
        assert _dir_exists('/tmp/_nonexistent_test_dir_12345') is False

    def test_file_exists_helper(self):
        with tempfile.NamedTemporaryFile() as f:
            assert _file_exists(f.name) is True
        assert _file_exists('/tmp/_nonexistent_file_12345') is False


class TestWriteRetry:
    """SD write retry logic."""

    def test_logger_init_preallocates_buffer(self):
        logger = FlightLogger()
        assert len(logger._write_buf) == 2 + FRAME_SIZE
        assert logger._write_buf[0] == 0xAA
        assert logger._write_buf[1] == 0x55

    def test_sd_failed_flag_starts_false(self):
        logger = FlightLogger()
        assert logger.sd_failed is False
