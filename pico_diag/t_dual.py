"""Test 6: Dual-Core Stress — Core 0+1 interference."""
import gc
import time
import config
from pico_diag import StreamStats, _header, _ok, _warn, _init_baro


def run():
    _header('Dual-Core Stress — Core 0+1 interference (60 sec)')

    baro = _init_baro()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine

    p0, _ = baro.read()
    duration_ms = 30000

    # Phase 1: Core 0 only
    print('\n  Phase 1: Core 0 only ({} sec)...'.format(duration_ms // 1000))
    k = AltitudeKalman()
    k.reset(0.0)
    fsm = FlightStateMachine()
    fsm.set_ground_reference(0.0)

    solo_stats = StreamStats()
    start = time.ticks_ms()
    count = 0
    while time.ticks_diff(time.ticks_ms(), start) < duration_ms:
        t0 = time.ticks_us()
        p, temp = baro.read()
        alt_raw = pressure_to_altitude(p, p0)
        alt_f, vel_f = k.update(alt_raw, 0.04)
        fsm.update(alt_f, vel_f, time.ticks_ms())
        t1 = time.ticks_us()
        solo_stats.add(time.ticks_diff(t1, t0))
        count += 1
    print('    {} frames'.format(count))

    # Phase 2: Core 0 + Timer LED (matches flight firmware architecture)
    print('  Phase 2: Core 0 + Timer LED ({} sec)...'.format(duration_ms // 1000))

    from machine import Pin, Timer
    led_pin = Pin(config.LED_PIN, Pin.OUT)
    _hb_time = [time.ticks_ms()]  # list so callback can mutate

    def _led_cb(t):
        led_pin.toggle()
        _hb_time[0] = time.ticks_ms()

    tmr = Timer(-1)  # virtual timer — RP2040 MicroPython only supports Timer(-1)
    tmr.init(period=25, mode=Timer.PERIODIC, callback=_led_cb)
    time.sleep_ms(100)

    k2 = AltitudeKalman()
    k2.reset(0.0)
    fsm2 = FlightStateMachine()
    fsm2.set_ground_reference(0.0)

    dual_stats = StreamStats()
    start = time.ticks_ms()
    count = 0
    last_hb_check = start
    core1_alive_s = 0.0

    while time.ticks_diff(time.ticks_ms(), start) < duration_ms:
        t0 = time.ticks_us()
        p, temp = baro.read()
        alt_raw = pressure_to_altitude(p, p0)
        alt_f, vel_f = k2.update(alt_raw, 0.04)
        fsm2.update(alt_f, vel_f, time.ticks_ms())
        t1 = time.ticks_us()
        dual_stats.add(time.ticks_diff(t1, t0))
        count += 1

        now = time.ticks_ms()
        if time.ticks_diff(now, last_hb_check) >= 1000:
            hb_age = time.ticks_diff(now, _hb_time[0])
            if hb_age < 500:
                core1_alive_s += 1.0
            last_hb_check = now

    tmr.deinit()
    led_pin.value(0)

    print('    {} frames'.format(count))

    print('\n  {:>16s}  {:>8s}  {:>8s}  {:>8s}'.format('', 'Avg(us)', 'Max(us)', 'Std(us)'))
    print('  {:<16s}  {:>8.0f}  {:>8.0f}  {:>8.1f}'.format(
        'Core 0 only:', solo_stats.mean, solo_stats.hi, solo_stats.std()))
    print('  {:<16s}  {:>8.0f}  {:>8.0f}  {:>8.1f}'.format(
        'Core 0+Timer:', dual_stats.mean, dual_stats.hi, dual_stats.std()))

    jitter_avg = dual_stats.mean - solo_stats.mean
    jitter_max = dual_stats.hi - solo_stats.hi
    print('\n  Jitter increase: {:+.0f} us avg, {:+.0f} us max'.format(jitter_avg, jitter_max))

    budget_us = 1_000_000 // config.SAMPLE_RATE_HZ
    if dual_stats.hi < budget_us:
        _ok('Within {} us budget'.format(budget_us))
    else:
        _warn('Max frame time ({:.0f} us) exceeds budget'.format(dual_stats.hi))

    print('  Timer LED alive: {:.0f} / {:.0f} seconds'.format(core1_alive_s, duration_ms / 1000))
    if core1_alive_s >= (duration_ms / 1000) - 2:
        _ok('Timer LED heartbeat stable')
    else:
        _warn('Timer LED heartbeat gaps detected')

    del k, k2, fsm, fsm2
    gc.collect()
