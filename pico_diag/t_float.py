"""Test 5: Float Precision — Kalman drift over 10000 iterations."""
import gc
from pico_diag import _header, _ok, _warn, _fail


def run():
    _header('Float Precision — Kalman drift over 10000 iterations')
    print('  (No hardware needed — pure math test)')

    from flight.kalman import AltitudeKalman

    n = 10000
    dt = 0.04

    # Test A: Constant input
    print('\n  Test A: Constant 500.0 m')
    print('  {:>6s}  {:>10s}  {:>10s}  {:>8s}'.format('Iter', 'Alt', 'Vel', 'Drift'))

    k = AltitudeKalman()
    k.reset(500.0)

    for i in range(1, n + 1):
        k.update(500.0, dt)
        if i % 1000 == 0:
            drift = abs(k.x_alt - 500.0)
            print('  {:>6d}  {:>10.3f}  {:>+10.4f}  {:>7.3f}m'.format(
                i, k.x_alt, k.x_vel, drift))

    drift_a = abs(k.x_alt - 500.0)
    if drift_a < 0.1:
        _ok('Constant drift: {:.4f} m'.format(drift_a))
    else:
        _warn('Constant drift: {:.4f} m'.format(drift_a))

    # Test B: Ramp input
    print('\n  Test B: Ramp 0 → {} m'.format(n))
    print('  {:>6s}  {:>10s}  {:>10s}'.format('Iter', 'Alt', 'Vel'))

    k2 = AltitudeKalman()
    k2.reset(0.0)

    for i in range(1, n + 1):
        k2.update(float(i), dt)
        if i % 1000 == 0:
            print('  {:>6d}  {:>10.2f}  {:>+10.3f}'.format(i, k2.x_alt, k2.x_vel))

    expected_vel = 1.0 / dt
    alt_err = abs(k2.x_alt - float(n))
    vel_err = abs(k2.x_vel - expected_vel)
    print('\n  Expected vel: {:.1f} m/s'.format(expected_vel))
    print('  Final: alt={:.2f}  vel={:.2f}'.format(k2.x_alt, k2.x_vel))
    print('  Alt err: {:.2f} m  Vel err: {:.2f} m/s'.format(alt_err, vel_err))

    if alt_err < 5.0:
        _ok('Ramp tracking')
    else:
        _warn('Ramp tracking error: {:.2f} m'.format(alt_err))

    # Test C: Covariance health
    print('\n  Covariance: P = [[{:.4f}, {:.4f}], [{:.4f}, {:.4f}]]'.format(
        k2.p00, k2.p01, k2.p10, k2.p11))
    if k2.p00 >= 0 and k2.p11 >= 0:
        _ok('Diagonal positive')
    else:
        _fail('Covariance matrix lost positive-definiteness!')

    del k, k2
    gc.collect()
