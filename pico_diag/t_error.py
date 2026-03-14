"""Test 8: Error Injection — fault tolerance verification."""
import gc
import time
import config
import pico_diag
from pico_diag import _header, _ok, _fail, _warn, _init_i2c, _init_baro, _init_sd


def run():
    _header('Error Injection — fault tolerance verification')

    # Test A: I2C wrong address
    print('\n  A: I2C wrong address...')
    try:
        i2c = _init_i2c()
        try:
            i2c.readfrom_mem(0x50, 0x00, 1)
            _warn('No error from wrong address (device at 0x50?)')
        except OSError:
            baro = _init_baro()
            p, t = baro.read()
            if p > 0:
                _ok('OSError caught, BMP180 OK after (P={:.0f} Pa)'.format(p))
            else:
                _fail('BMP180 returned bad data after error')
    except Exception as e:
        _fail('Unexpected: {}'.format(e))

    # Test B: I2C bus recovery
    print('\n  B: I2C bus recovery...')
    try:
        pico_diag._i2c = None
        pico_diag._baro = None
        gc.collect()
        time.sleep_ms(50)
        from machine import SoftI2C, Pin
        pico_diag._i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                       freq=config.I2C_FREQ, timeout=config.I2C_TIMEOUT_US)
        from sensors.barometer import BMP180
        pico_diag._baro = BMP180(pico_diag._i2c, config.BMP180_ADDR)
        p, t = pico_diag._baro.read()
        _ok('Re-init success, BMP180 OK (P={:.0f} Pa)'.format(p))
    except Exception as e:
        _fail('Bus recovery failed: {}'.format(e))
        try:
            pico_diag._i2c = None
            pico_diag._baro = None
            _init_baro()
        except Exception:
            pass

    # Test C: SD unmount/remount
    print('\n  C: SD unmount/remount...')
    if _init_sd():
        from logging.sdcard_mount import unmount, mount, is_mounted
        try:
            with open('/sd/_diag_err_test.tmp', 'wb') as f:
                f.write(b'test')
            _ok('Write before unmount OK')

            unmount()
            pico_diag._sd_mounted = None

            write_failed = False
            try:
                with open('/sd/_diag_err_test.tmp', 'wb') as f:
                    f.write(b'test')
            except OSError:
                write_failed = True

            if write_failed:
                _ok('Write after unmount failed as expected')
            else:
                _warn('Write after unmount did not fail')

            import os
            if mount():
                pico_diag._sd_mounted = True
                try:
                    with open('/sd/_diag_err_test.tmp', 'wb') as f:
                        f.write(b'test2')
                    _ok('Remount + write OK')
                except Exception as e:
                    _fail('Write after remount failed: {}'.format(e))
            else:
                _fail('Remount failed')
                pico_diag._sd_mounted = False

            try:
                os.remove('/sd/_diag_err_test.tmp')
            except OSError:
                pass
        except Exception as e:
            _fail('SD test error: {}'.format(e))
    else:
        _warn('SD not mounted — skipping')

    # Test D: Kalman bad input
    print('\n  D: Kalman bad input...')
    from flight.kalman import AltitudeKalman
    k = AltitudeKalman()
    k.reset(100.0)

    tests_d = [
        ('inf', float('inf')),
        ('-inf', float('-inf')),
        ('1e15', 1e15),
    ]
    all_ok = True
    for name, val in tests_d:
        try:
            a, v = k.update(val, 0.04)
            print('    {}: alt={}, vel={}'.format(name, a, v))
        except Exception as e:
            _fail('{} caused crash: {}'.format(name, e))
            all_ok = False
    if all_ok:
        _ok('No crashes from bad Kalman input')

    # Test E: FSM extreme values
    print('\n  E: FSM extreme values...')
    from flight.state_machine import FlightStateMachine
    f = FlightStateMachine()
    f.set_ground_reference(0.0)

    extremes = [
        (99999.0, 99999.0),
        (-99999.0, -99999.0),
        (0.0, 0.0),
        (1e10, -1e10),
    ]
    all_ok = True
    for alt, vel in extremes:
        try:
            state = f.update(alt, vel, time.ticks_ms())
        except Exception as e:
            _fail('alt={}, vel={} crashed: {}'.format(alt, vel, e))
            all_ok = False
    if all_ok:
        _ok('No crashes from extreme FSM values (final state={})'.format(f.state_name))

    del k, f
    gc.collect()
