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

    from logging.datalog import FRAME_FORMAT, FRAME_SIZE
    pack_buf = bytearray(2 + FRAME_SIZE)
    fmt = FRAME_FORMAT
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
        struct.pack_into(fmt, pack_buf, 2, 0, 0, p, temp, alt_raw, alt_f, vel_f, v3, v5, v9, 0, 0, 0, 0, 0, 0, 0)
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

    # ── Phase 2: Pipelined baro reads ────────────────────
    # Measures the same pipeline but with conversion overlapping spin-wait.
    # This matches the actual main.py flight loop architecture.
    # Fewer frames than Phase 1 since each includes a real-time sleep.
    n2 = 250
    print('\n\n  Pipelined Budget ({} frames, {} Hz = {} us):'.format(n2, config.SAMPLE_RATE_HZ, budget_us))
    print('  (pressure conversion runs during spin-wait between frames)')

    kalman2 = AltitudeKalman()
    fsm2 = FlightStateMachine()
    kalman2.reset(0.0)
    fsm2.set_ground_reference(0.0)

    tp_collect = StreamStats()
    tp_extra = StreamStats()
    tp_alt = StreamStats()
    tp_kalman = StreamStats()
    tp_fsm = StreamStats()
    tp_power = StreamStats()
    tp_pack = StreamStats()
    tp_total = StreamStats()
    tp_temp = StreamStats()  # occasional blocking temp read

    extra_reads = config.BARO_AVG_EXTRA

    # Prime the pipeline — one blocking temp read + kick off first pressure
    raw_UT = baro._read_raw_temp()
    baro.start(temp=False)
    temp_every = config.SAMPLE_RATE_HZ
    temp_counter = 0

    print('  Running {} frames ({}+{} samples/frame) '.format(n2, 1, extra_reads), end='')
    for i in range(n2):
        # Wait for conversion to finish (simulates spin-wait in real loop)
        time.sleep_ms(1_000 // config.SAMPLE_RATE_HZ)

        frame_start = time.ticks_us()

        # Collect pressure (just I2C read — no sleep)
        t0 = time.ticks_us()
        raw_UP = baro.collect()
        pressure2, temperature2 = baro.compensate(raw_UT, raw_UP)
        t1 = time.ticks_us()
        tp_collect.add(time.ticks_diff(t1, t0))

        # Extra fast reads at OSS=0 for averaging
        t0 = time.ticks_us()
        for _ in range(extra_reads):
            pressure2 += baro.read_extra(raw_UT)
        if extra_reads:
            pressure2 /= (1 + extra_reads)
        t1 = time.ticks_us()
        if extra_reads:
            tp_extra.add(time.ticks_diff(t1, t0))

        # Occasional blocking temp re-read (~5ms, once per second)
        temp_counter += 1
        if temp_counter >= temp_every:
            temp_counter = 0
            t0 = time.ticks_us()
            raw_UT = baro._read_raw_temp()
            t1 = time.ticks_us()
            tp_temp.add(time.ticks_diff(t1, t0))

        # Kick off next pressure conversion (runs during spin-wait)
        baro.start(temp=False)

        t0 = time.ticks_us()
        alt_raw2 = pressure_to_altitude(pressure2, p0)
        t1 = time.ticks_us()
        tp_alt.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        alt_f2, vel_f2 = kalman2.update(alt_raw2, 0.04)
        t1 = time.ticks_us()
        tp_kalman.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        state2 = fsm2.update(alt_f2, vel_f2, time.ticks_ms())
        t1 = time.ticks_us()
        tp_fsm.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        v3, v5, v9 = power.read_all()
        t1 = time.ticks_us()
        tp_power.add(time.ticks_diff(t1, t0))

        t0 = time.ticks_us()
        struct.pack_into(fmt, pack_buf, 2, 0, 0, pressure2, temperature2,
                         alt_raw2, alt_f2, vel_f2, v3, v5, v9, 0, 0, 0, 0, 0, 0, 0)
        t1 = time.ticks_us()
        tp_pack.add(time.ticks_diff(t1, t0))

        frame_end = time.ticks_us()
        tp_total.add(time.ticks_diff(frame_end, frame_start))

        if (i + 1) % 50 == 0:
            print('.', end='')
    print(' done')

    print()
    print('  {:<16s} {:>8s}  {:>8s}  {:>8s}'.format('Stage', 'Avg(us)', 'Max(us)', '% Budget'))
    print('  ' + '─' * 46)

    stages2 = [
        ('Collect+comp', tp_collect),
    ]
    if extra_reads:
        stages2.append(('Avg {}x OSS=0'.format(extra_reads), tp_extra))
    stages2 += [
        ('Alt calc', tp_alt),
        ('Kalman', tp_kalman),
        ('FSM', tp_fsm),
        ('Power read', tp_power),
        ('Struct pack', tp_pack),
    ]
    for name, s in stages2:
        pct = (s.mean / budget_us) * 100
        print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format(name, s.mean, s.hi, pct))

    print('  ' + '─' * 46)
    pct_total2 = (tp_total.mean / budget_us) * 100
    headroom2 = budget_us - tp_total.mean
    print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format('TOTAL', tp_total.mean, tp_total.hi, pct_total2))
    print('  {:<16s} {:>8.0f}  {:>8.0f}  {:>7.1f}%'.format(
        'Headroom', headroom2, budget_us - tp_total.hi,
        ((budget_us - tp_total.mean) / budget_us) * 100))

    if tp_temp.n > 0:
        print('\n  Temp re-read: {:.0f} us avg, {:.0f} us max ({} reads in {} frames)'.format(
            tp_temp.mean, tp_temp.hi, tp_temp.n, n2))

    # Summary comparison
    print('\n  ── Blocking vs Pipelined ──')
    print('  {:<16s} {:>10s}  {:>10s}'.format('', 'Blocking', 'Pipelined'))
    print('  {:<16s} {:>9.0f}us  {:>9.0f}us'.format('Avg frame', t_total.mean, tp_total.mean))
    print('  {:<16s} {:>9.0f}us  {:>9.0f}us'.format('Max frame', t_total.hi, tp_total.hi))
    print('  {:<16s} {:>9.1f}%   {:>9.1f}%'.format('Budget used', pct_total, pct_total2))
    speedup = t_total.mean / tp_total.mean if tp_total.mean > 0 else 0
    print('  Speedup: {:.1f}x'.format(speedup))

    if tp_total.hi > budget_us:
        _warn('Pipelined max ({:.0f} us) exceeds budget ({} us)'.format(tp_total.hi, budget_us))
    else:
        _ok('Pipelined: all frames within budget')
