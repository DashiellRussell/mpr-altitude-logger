"""Test 4: RAM Profile — memory usage + leak detection."""
import gc
import time
from pico_diag import _header, _ok, _warn, _init_baro


def run():
    _header('RAM Profile — memory usage + leak detection')

    gc.collect()
    total = gc.mem_free() + gc.mem_alloc()
    after_imports = gc.mem_free()
    print('  Total available:  {:>8d} bytes'.format(total))
    print('  After imports:    {:>8d} bytes free'.format(after_imports))

    print('\n  {:<24s} {:>10s}'.format('Object', 'Size (bytes)'))

    # Kalman
    gc.collect()
    before = gc.mem_free()
    from flight.kalman import AltitudeKalman
    k = AltitudeKalman()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    print('  {:<24s} {:>10d}'.format('AltitudeKalman', sz))

    # FSM
    gc.collect()
    before = gc.mem_free()
    from flight.state_machine import FlightStateMachine
    f = FlightStateMachine()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    print('  {:<24s} {:>10d}'.format('FlightStateMachine', sz))

    # FlightLogger
    gc.collect()
    before = gc.mem_free()
    from logging.datalog import FlightLogger
    lg = FlightLogger()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    print('  {:<24s} {:>10d}'.format('FlightLogger', sz))

    # BMP180
    gc.collect()
    before = gc.mem_free()
    baro = _init_baro()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    print('  {:<24s} {:>10d}'.format('BMP180 + cal', sz))

    gc.collect()
    after_objects = gc.mem_free()
    print('\n  After all objects: {:>8d} bytes free'.format(after_objects))

    # Hot loop leak detection
    print('\n  Hot Loop (1000 frames):')
    print('  {:>6s}  {:>10s}  {:>8s}'.format('Iter', 'Free', 'Delta'))

    from sensors.barometer import pressure_to_altitude
    p0, _ = baro.read()
    k.reset(0.0)
    f.set_ground_reference(0.0)

    gc.collect()
    baseline = gc.mem_free()
    checkpoints = [baseline]
    print('  {:>6d}  {:>10d}  {:>8s}'.format(0, baseline, '---'))

    for i in range(1, 1001):
        p, t = baro.read()
        alt_raw = pressure_to_altitude(p, p0)
        alt_f, vel_f = k.update(alt_raw, 0.04)
        f.update(alt_f, vel_f, time.ticks_ms())

        if i % 100 == 0:
            gc.collect()
            free = gc.mem_free()
            delta = free - checkpoints[-1]
            checkpoints.append(free)
            print('  {:>6d}  {:>10d}  {:>+8d}'.format(i, free, delta))

    total_leak = checkpoints[-1] - checkpoints[0]
    print()
    if abs(total_leak) <= 100:
        _ok('Leak rate: {} bytes / 1000 frames — negligible'.format(total_leak))
    else:
        _warn('Leak rate: {} bytes / 1000 frames'.format(total_leak))

    del k, f, lg
    gc.collect()
