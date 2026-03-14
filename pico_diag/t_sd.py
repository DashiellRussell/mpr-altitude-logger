"""Test 2: SD Card Bench — write/flush latency."""
import os
import time
from pico_diag import StreamStats, Histogram, _header, _ok, _fail, _warn, _init_sd


def run():
    _header('SD Card Bench — write/flush latency')

    if not _init_sd():
        _fail('SD card not mounted')
        return

    fname = '/sd/_diag_bench.tmp'
    buf = bytearray(34)
    for i in range(34):
        buf[i] = i & 0xFF

    # Phase 1: Quick burst (1000 frames)
    print('\n  Phase 1: Quick burst (1000 frames)')
    write_stats = StreamStats()
    flush_stats = StreamStats()
    write_hist = Histogram((50, 100, 200, 500, 1000, 2000, 5000))

    try:
        f = open(fname, 'wb')
        for i in range(1000):
            t0 = time.ticks_us()
            f.write(buf)
            t1 = time.ticks_us()
            dt = time.ticks_diff(t1, t0)
            write_stats.add(dt)
            write_hist.add(dt)

            if (i + 1) % 25 == 0:
                ft0 = time.ticks_us()
                f.flush()
                ft1 = time.ticks_us()
                flush_stats.add(time.ticks_diff(ft1, ft0))

        sync_stats = StreamStats()
        if hasattr(os, 'sync'):
            for _ in range(10):
                st0 = time.ticks_us()
                os.sync()
                st1 = time.ticks_us()
                sync_stats.add(time.ticks_diff(st1, st0))

        f.close()
    except Exception as e:
        _fail('Write error: {}'.format(e))
        return

    write_stats.report('Write', 'us')
    flush_stats.report('Flush', 'us')
    if sync_stats.n > 0:
        sync_stats.report('Sync', 'us')

    print('\n  Write Distribution (us):')
    write_hist.print_chart()

    if write_stats.hi > 40000:
        _warn('Max write ({:.0f} us) exceeds 40ms frame budget!'.format(write_stats.hi))
    else:
        _ok('All writes within 40ms frame budget')

    # Phase 2: Sustained (5 minutes at 25 Hz)
    print('\n  Phase 2: Sustained write (5 min at 25 Hz)')
    print('  {:>6s}  {:>8s}  {:>8s}  {:>8s}  {:>4s}'.format(
        'Time', 'Avg(us)', 'Max(us)', 'Bytes', 'Err'))

    total_bytes = 0
    total_errors = 0
    interval_s = 30
    duration_s = 300
    frame_interval_us = 40000

    try:
        f = open(fname, 'wb')
        run_start = time.ticks_ms()
        interval_stats = StreamStats()
        interval_errors = 0
        interval_bytes = 0
        next_frame = time.ticks_us()
        interval_start = time.ticks_ms()

        while True:
            now_ms = time.ticks_ms()
            elapsed_s = time.ticks_diff(now_ms, run_start) // 1000
            if elapsed_s >= duration_s:
                break

            now_us = time.ticks_us()
            if time.ticks_diff(now_us, next_frame) < 0:
                continue
            next_frame = time.ticks_add(now_us, frame_interval_us)

            try:
                t0 = time.ticks_us()
                f.write(buf)
                t1 = time.ticks_us()
                interval_stats.add(time.ticks_diff(t1, t0))
                interval_bytes += 34
            except OSError:
                interval_errors += 1

            if interval_stats.n % 25 == 0:
                try:
                    f.flush()
                except OSError:
                    interval_errors += 1

            if time.ticks_diff(now_ms, interval_start) >= interval_s * 1000:
                m = elapsed_s // 60
                s = elapsed_s % 60
                total_bytes += interval_bytes
                total_errors += interval_errors
                print('  {:>2d}:{:02d}  {:>8.0f}  {:>8.0f}  {:>8d}  {:>4d}'.format(
                    m, s, interval_stats.mean, interval_stats.hi,
                    interval_bytes, interval_errors))
                interval_stats = StreamStats()
                interval_errors = 0
                interval_bytes = 0
                interval_start = now_ms

        f.close()
    except Exception as e:
        _fail('Sustained test error: {}'.format(e))

    try:
        os.remove(fname)
    except OSError:
        pass

    if total_errors == 0:
        _ok('No write errors in {} bytes'.format(total_bytes))
    else:
        _fail('{} write errors in {} bytes'.format(total_errors, total_bytes))
