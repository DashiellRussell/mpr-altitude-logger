"""Test 7: Endurance Run — full pipeline stability (10 min)."""
import gc
import os
import time
import struct
import config
from pico_diag import StreamStats, _header, _ok, _warn, _fail, _init_baro, _init_power, _init_sd


def run():
    _header('Endurance Run — full pipeline stability (10 min)')
    print('  Ctrl+C to abort (partial results shown)\n')

    baro = _init_baro()
    power = _init_power()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine
    from logging.datalog import FRAME_FORMAT

    sd_ok = _init_sd()

    k = AltitudeKalman()
    fsm = FlightStateMachine()

    p0, _ = baro.read()
    k.reset(0.0)
    fsm.set_ground_reference(0.0)

    pack_buf = bytearray(34)
    sd_file = None
    sd_fname = '/sd/_diag_endurance.tmp'

    if sd_ok:
        try:
            sd_file = open(sd_fname, 'wb')
        except OSError:
            sd_ok = False

    duration_s = 600
    interval_s = 30
    frame_interval_us = 1_000_000 // config.SAMPLE_RATE_HZ

    print('  {:>6s}  {:>8s}  {:>8s}  {:>10s}  {:>7s}  {:>6s}'.format(
        'Time', 'Avg(us)', 'Max(us)', 'RAM(free)', 'Temp(C)', 'Errors'))

    total_frames = 0
    total_errors = 0
    all_intervals = []

    run_start = time.ticks_ms()
    interval_start = run_start
    interval_stats = StreamStats()
    interval_errors = 0
    last_temp = 0.0
    next_frame = time.ticks_us()

    try:
        while True:
            now_ms = time.ticks_ms()
            elapsed_s = time.ticks_diff(now_ms, run_start) // 1000
            if elapsed_s >= duration_s:
                break

            now_us = time.ticks_us()
            if time.ticks_diff(now_us, next_frame) < 0:
                continue
            next_frame = time.ticks_add(now_us, frame_interval_us)

            t0 = time.ticks_us()
            try:
                p, temp = baro.read()
                last_temp = temp
                alt_raw = pressure_to_altitude(p, p0)
                alt_f, vel_f = k.update(alt_raw, 0.04)
                fsm.update(alt_f, vel_f, now_ms)
                v3, v5, v9 = power.read_all()

                if sd_file is not None:
                    struct.pack_into(FRAME_FORMAT, pack_buf, 2,
                                     now_ms, 0, p, temp, alt_raw, alt_f, vel_f, v3, v5, v9, 0)
                    sd_file.write(pack_buf)

                total_frames += 1
            except Exception:
                interval_errors += 1
                total_errors += 1

            t1 = time.ticks_us()
            interval_stats.add(time.ticks_diff(t1, t0))

            if sd_file is not None and total_frames % 25 == 0:
                try:
                    sd_file.flush()
                except OSError:
                    pass

            if time.ticks_diff(now_ms, interval_start) >= interval_s * 1000:
                gc.collect()
                free = gc.mem_free()
                m = elapsed_s // 60
                s = elapsed_s % 60
                print('  {:>2d}:{:02d}  {:>8.0f}  {:>8.0f}  {:>10d}  {:>7.1f}  {:>6d}'.format(
                    m, s, interval_stats.mean, interval_stats.hi,
                    free, last_temp, interval_errors))
                all_intervals.append((interval_stats.mean, interval_stats.hi, free, last_temp, interval_errors))
                interval_stats = StreamStats()
                interval_errors = 0
                interval_start = now_ms

    except KeyboardInterrupt:
        print('\n  [Aborted by user]')

    if sd_file is not None:
        try:
            sd_file.flush()
            sd_file.close()
        except OSError:
            pass
        try:
            os.remove(sd_fname)
        except OSError:
            pass

    print('\n  Total frames: {}'.format(total_frames))

    if len(all_intervals) >= 2:
        first = all_intervals[0]
        last = all_intervals[-1]
        timing_drift = last[0] - first[0]
        ram_change = last[2] - first[2]
        temp_drift = last[3] - first[3]

        print('  Timing drift: {:+.0f} us ({:+.1f}%)'.format(
            timing_drift, (timing_drift / first[0]) * 100 if first[0] > 0 else 0))
        print('  RAM change: {:+d} bytes'.format(ram_change))
        print('  Temp drift: {:+.1f} C'.format(temp_drift))

        if abs(timing_drift) < 500:
            _ok('Timing stable')
        else:
            _warn('Timing drift: {:+.0f} us'.format(timing_drift))

        if abs(ram_change) < 200:
            _ok('RAM stable')
        else:
            _warn('RAM change: {:+d} bytes'.format(ram_change))

    print('  Errors: {}'.format(total_errors))
    if total_errors == 0:
        _ok('No errors')

    del k, fsm
    gc.collect()
