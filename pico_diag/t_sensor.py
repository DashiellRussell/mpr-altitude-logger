"""Test 1: Sensor Bench — BMP180 I2C timing + pressure noise."""
import time
from pico_diag import StreamStats, Histogram, _header, _ok, _warn, _init_baro


def run():
    _header('Sensor Bench — BMP180 timing + noise (1000 reads)')

    baro = _init_baro()
    n_samples = 1000

    timing = StreamStats()
    pressure_stats = StreamStats()
    temp_stats = StreamStats()
    hist = Histogram((25000, 28000, 30000, 32000, 34000, 36000, 38000, 40000, 50000, 100000))

    print('  Reading {} samples '.format(n_samples), end='')
    for i in range(n_samples):
        t0 = time.ticks_us()
        p, t = baro.read()
        t1 = time.ticks_us()
        dt = time.ticks_diff(t1, t0)

        timing.add(dt)
        hist.add(dt)
        pressure_stats.add(p)
        temp_stats.add(t)

        if (i + 1) % 100 == 0:
            print('.', end='')
    print(' done')

    print()
    timing.report('Read Timing', 'us')

    print('\n  Timing Distribution (us):')
    hist.print_chart()

    p_std = pressure_stats.std()
    alt_noise = p_std * 0.083
    print('\n  Pressure Noise:')
    print('    Std: {:.1f} Pa  (~{:.2f} m altitude noise)'.format(p_std, alt_noise))
    print('    Range: {:.0f}-{:.0f} Pa ({:.0f} Pa p2p)'.format(
        pressure_stats.lo, pressure_stats.hi, pressure_stats.hi - pressure_stats.lo))

    print('\n  Temperature:')
    print('    Avg: {:.1f} C  Std: {:.2f} C'.format(temp_stats.mean, temp_stats.std()))

    avg = timing.mean
    stretches = 0
    threshold = avg * 2
    for i in range(len(hist.edges) + 1):
        edge_val = hist.edges[i] if i < len(hist.edges) else threshold
        if edge_val >= threshold:
            stretches += hist.bins[i]
    if stretches > 0:
        _warn('{} reads > 2x average ({:.0f} us) — clock stretching'.format(stretches, threshold))
    else:
        _ok('No clock stretch events detected')
