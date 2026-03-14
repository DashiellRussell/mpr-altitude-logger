"""Test 3: Loop Budget — per-stage pipeline timing."""
import time
import struct
import config
from pico_diag import StreamStats, _header, _ok, _warn, _init_baro, _init_power


def run():
    _header('Loop Budget — per-stage pipeline timing (1000 frames)')

    baro = _init_baro()
    power = _init_power()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine

    kalman = AltitudeKalman()
    fsm = FlightStateMachine()

    p0, _ = baro.read()
    kalman.reset(0.0)
    fsm.set_ground_reference(0.0)

    t_baro = StreamStats()
    t_alt = StreamStats()
    t_kalman = StreamStats()
    t_fsm = StreamStats()
    t_power = StreamStats()
    t_pack = StreamStats()
    t_total = StreamStats()

    pack_buf = bytearray(34)
    fmt = '<IBfffffHHHB'
    n = 1000

    print('  Running {} frames '.format(n), end='')
    for i in range(n):
        frame_start = time.ticks_us()

        t0 = time.ticks_us()
        p, temp = baro.read()
        t1 = time.ticks_us()
        t_baro.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        alt_raw = pressure_to_altitude(p, p0)
        t1 = time.ticks_us()
        t_alt.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        alt_f, vel_f = kalman.update(alt_raw, 0.04)
        t1 = time.ticks_us()
        t_kalman.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        state = fsm.update(alt_f, vel_f, time.ticks_ms())
        t1 = time.ticks_us()
        t_fsm.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        v3, v5, v9 = power.read_all()
        t1 = time.ticks_us()
        t_power.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        struct.pack_into(fmt, pack_buf, 2, 0, 0, p, temp, alt_raw, alt_f, vel_f, v3, v5, v9, 0)
        t1 = time.ticks_us()
        t_pack.add(time.ticks_diff(t1, t0))

        frame_end = time.ticks_us()
        t_total.add(time.ticks_diff(frame_end, frame_start))

        if (i + 1) % 100 == 0:
            print('.', end='')
    print(' done')

    budget_us = 1_000_000 // config.SAMPLE_RATE_HZ

    print('\n  Pipeline Budget ({} frames, {} Hz = {} us):'.format(n, config.SAMPLE_RATE_HZ, budget_us))
    print()
    print('  {:<16s} {:>8s}  {:>8s}  {:>8s}'.format('Stage', 'Avg(us)', 'Max(us)', '% Budget'))
    print('  ' + '─' * 46)

    stages = [
        ('Baro read', t_baro),
        ('Alt calc', t_alt),
        ('Kalman', t_kalman),
        ('FSM', t_fsm),
        ('Power read', t_power),
        ('Struct pack', t_pack),
    ]
    for name, s in stages:
        pct = (s.mean / budget_us) * 100
        print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format(name, s.mean, s.hi, pct))

    print('  ' + '─' * 46)
    pct_total = (t_total.mean / budget_us) * 100
    headroom = budget_us - t_total.mean
    print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format('TOTAL', t_total.mean, t_total.hi, pct_total))
    print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format(
        'Headroom', headroom, budget_us - t_total.hi,
        ((budget_us - t_total.mean) / budget_us) * 100))

    if t_total.hi > budget_us:
        _warn('Max frame time ({:.0f} us) exceeds budget ({} us)!'.format(t_total.hi, budget_us))
    else:
        _ok('All frames within budget')
