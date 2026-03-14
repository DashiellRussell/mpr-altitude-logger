"""
Pico Diagnostic TUI — on-device hardware stress testing.

Tests real I2C timing, SD write latency, MicroPython float precision,
RAM pressure, dual-core interference, and error recovery.

Run on the Pico via REPL: import pico_diag

Ctrl+C returns to menu from any test.
"""

import gc
import os
import time
import struct

import config

DIAG_VERSION = "1.0.0"

# ── ANSI helpers ──────────────────────────────────────────
CLEAR = '\x1b[2J\x1b[H'
BOLD = '\x1b[1m'
RESET = '\x1b[0m'
RED = '\x1b[31m'
GREEN = '\x1b[32m'
YELLOW = '\x1b[33m'
CYAN = '\x1b[36m'


def _ok(msg):
    print('  {}[OK]{} {}'.format(GREEN, RESET, msg))


def _fail(msg):
    print('  {}[FAIL]{} {}'.format(RED, RESET, msg))


def _warn(msg):
    print('  {}[WARN]{} {}'.format(YELLOW, RESET, msg))


def _header(title):
    print('\n{}{}  {} {}'.format(BOLD, CYAN, title, RESET))
    print('  ' + '─' * 50)


# ── StreamStats (Welford's online algorithm) ─────────────
class StreamStats:
    __slots__ = ('n', 'mean', '_m2', 'lo', 'hi')

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.lo = 1e30
        self.hi = -1e30

    def add(self, x):
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self._m2 += d * (x - self.mean)
        if x < self.lo:
            self.lo = x
        if x > self.hi:
            self.hi = x

    def std(self):
        if self.n < 2:
            return 0.0
        v = self._m2 / self.n
        # sqrt via Newton's method (no math module needed)
        if v <= 0:
            return 0.0
        s = v
        for _ in range(10):
            s = 0.5 * (s + v / s)
        return s

    def report(self, label, unit=''):
        u = ' ' + unit if unit else ''
        print('  {:<18s} min={:.1f}{}  avg={:.1f}{}  max={:.1f}{}  std={:.1f}{}'.format(
            label, self.lo, u, self.mean, u, self.hi, u, self.std(), u))


# ── Histogram (fixed bins) ──────────────────────────────
class Histogram:
    def __init__(self, edges):
        self.edges = edges
        self.bins = [0] * (len(edges) + 1)  # last = overflow

    def add(self, x):
        for i in range(len(self.edges)):
            if x < self.edges[i]:
                self.bins[i] += 1
                return
        self.bins[len(self.edges)] += 1

    def print_chart(self):
        mx = max(self.bins) if max(self.bins) > 0 else 1
        for i in range(len(self.edges) + 1):
            if i == 0:
                label = '<{}'.format(self.edges[0])
            elif i < len(self.edges):
                label = '{}-{}'.format(self.edges[i - 1], self.edges[i])
            else:
                label = '{}+'.format(self.edges[-1])
            bar_len = (self.bins[i] * 30) // mx
            bar = '#' * bar_len
            cnt = self.bins[i]
            if cnt > 0:
                print('  {:>8s} |{:<30s} ({})'.format(label, bar, cnt))


# ── Lazy hardware init ──────────────────────────────────
_baro = None
_i2c = None
_power = None
_sd_mounted = None


def _init_i2c():
    global _i2c
    if _i2c is not None:
        return _i2c
    from machine import SoftI2C, Pin
    _i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                   freq=config.I2C_FREQ, timeout=config.I2C_TIMEOUT_US)
    return _i2c


def _init_baro():
    global _baro
    if _baro is not None:
        return _baro
    from sensors.barometer import BMP180
    i2c = _init_i2c()
    _baro = BMP180(i2c, config.BMP180_ADDR)
    return _baro


def _init_power():
    global _power
    if _power is not None:
        return _power
    from sensors.power import PowerMonitor
    _power = PowerMonitor()
    return _power


def _init_sd():
    global _sd_mounted
    if _sd_mounted is not None:
        return _sd_mounted
    from logging.sdcard_mount import mount, is_mounted
    if is_mounted():
        _sd_mounted = True
    else:
        _sd_mounted = mount()
    return _sd_mounted


# ── Test 1: Sensor Bench ────────────────────────────────
def test_sensor_bench():
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

    # Pressure noise → altitude noise
    p_std = pressure_stats.std()
    # Approximate: 1 Pa ≈ 0.083m at sea level
    alt_noise = p_std * 0.083
    print('\n  Pressure Noise:')
    print('    Std: {:.1f} Pa  (~{:.2f} m altitude noise)'.format(p_std, alt_noise))
    print('    Range: {:.0f}-{:.0f} Pa ({:.0f} Pa p2p)'.format(
        pressure_stats.lo, pressure_stats.hi, pressure_stats.hi - pressure_stats.lo))

    print('\n  Temperature:')
    print('    Avg: {:.1f} C  Std: {:.2f} C'.format(temp_stats.mean, temp_stats.std()))

    # Clock stretch detection
    avg = timing.mean
    stretches = 0
    threshold = avg * 2
    # Re-count from histogram overflow bins
    for i in range(len(hist.edges) + 1):
        edge_val = hist.edges[i] if i < len(hist.edges) else threshold
        if edge_val >= threshold:
            stretches += hist.bins[i]
    if stretches > 0:
        _warn('{} reads > 2x average ({:.0f} us) — clock stretching'.format(stretches, threshold))
    else:
        _ok('No clock stretch events detected')


# ── Test 2: SD Card Bench ───────────────────────────────
def test_sd_bench():
    _header('SD Card Bench — write/flush latency')

    if not _init_sd():
        _fail('SD card not mounted')
        return

    fname = '/sd/_diag_bench.tmp'
    buf = bytearray(34)  # Match FlightLogger frame size
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

        # Sync timing
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

    # Check budget: at 25Hz, frame budget is 40ms
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
    frame_interval_us = 40000  # 25 Hz

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

            # Flush every 25 frames
            if interval_stats.n % 25 == 0:
                try:
                    f.flush()
                except OSError:
                    interval_errors += 1

            # Report every interval
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

    # Cleanup
    try:
        os.remove(fname)
    except OSError:
        pass

    if total_errors == 0:
        _ok('No write errors in {} bytes'.format(total_bytes))
    else:
        _fail('{} write errors in {} bytes'.format(total_errors, total_bytes))


# ── Test 3: Loop Budget ─────────────────────────────────
def test_loop_budget():
    _header('Loop Budget — per-stage pipeline timing (1000 frames)')

    baro = _init_baro()
    power = _init_power()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine

    kalman = AltitudeKalman()
    fsm = FlightStateMachine()

    # Ground calibration (quick)
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

        # Baro read
        t0 = time.ticks_us()
        p, temp = baro.read()
        t1 = time.ticks_us()
        t_baro.add(time.ticks_diff(t1, t0))

        # Altitude calc
        t0 = time.ticks_us()
        alt_raw = pressure_to_altitude(p, p0)
        t1 = time.ticks_us()
        t_alt.add(time.ticks_diff(t1, t0))

        # Kalman
        t0 = time.ticks_us()
        alt_f, vel_f = kalman.update(alt_raw, 0.04)
        t1 = time.ticks_us()
        t_kalman.add(time.ticks_diff(t1, t0))

        # FSM
        t0 = time.ticks_us()
        state = fsm.update(alt_f, vel_f, time.ticks_ms())
        t1 = time.ticks_us()
        t_fsm.add(time.ticks_diff(t1, t0))

        # Power
        t0 = time.ticks_us()
        v3, v5, v9 = power.read_all()
        t1 = time.ticks_us()
        t_power.add(time.ticks_diff(t1, t0))

        # Struct pack
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


# ── Test 4: RAM Profile ─────────────────────────────────
def test_ram_profile():
    _header('RAM Profile — memory usage + leak detection')

    gc.collect()
    total = gc.mem_free() + gc.mem_alloc()
    after_imports = gc.mem_free()
    print('  Total available:  {:>8d} bytes'.format(total))
    print('  After imports:    {:>8d} bytes free'.format(after_imports))

    # Measure object sizes
    print('\n  {:<24s} {:>10s}'.format('Object', 'Size (bytes)'))

    objects = []

    # Kalman
    gc.collect()
    before = gc.mem_free()
    from flight.kalman import AltitudeKalman
    k = AltitudeKalman()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    objects.append(('AltitudeKalman', sz, k))
    print('  {:<24s} {:>10d}'.format('AltitudeKalman', sz))

    # FSM
    gc.collect()
    before = gc.mem_free()
    from flight.state_machine import FlightStateMachine
    f = FlightStateMachine()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    objects.append(('FlightStateMachine', sz, f))
    print('  {:<24s} {:>10d}'.format('FlightStateMachine', sz))

    # FlightLogger
    gc.collect()
    before = gc.mem_free()
    from logging.datalog import FlightLogger
    lg = FlightLogger()
    gc.collect()
    after = gc.mem_free()
    sz = before - after
    objects.append(('FlightLogger', sz, lg))
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

    # Cleanup
    del k, f, lg
    gc.collect()


# ── Test 5: Float Precision ─────────────────────────────
def test_float_precision():
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

    expected_vel = 1.0 / dt  # 25.0 m/s
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


# ── Test 6: Dual-Core Stress ────────────────────────────
def test_dual_core():
    _header('Dual-Core Stress — Core 0+1 interference (60 sec)')

    baro = _init_baro()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine

    p0, _ = baro.read()
    duration_ms = 30000  # 30s per phase

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

    # Phase 2: Core 0 + Core 1
    print('  Phase 2: Core 0 + Core 1 ({} sec)...'.format(duration_ms // 1000))

    import _thread
    global _core1_stop, _core1_heartbeat
    _core1_stop = False
    _core1_heartbeat = time.ticks_ms()

    def _stress_core1():
        global _core1_stop, _core1_heartbeat
        from machine import Pin
        led = Pin(config.LED_PIN, Pin.OUT)
        while not _core1_stop:
            led.toggle()
            _core1_heartbeat = time.ticks_ms()
            time.sleep_ms(25)
        led.value(0)

    _thread.start_new_thread(_stress_core1, ())
    time.sleep_ms(100)  # Let Core 1 start

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

        # Check Core 1 heartbeat every second
        now = time.ticks_ms()
        if time.ticks_diff(now, last_hb_check) >= 1000:
            hb_age = time.ticks_diff(now, _core1_heartbeat)
            if hb_age < 500:
                core1_alive_s += 1.0
            last_hb_check = now

    # Stop Core 1
    _core1_stop = True
    time.sleep_ms(100)

    print('    {} frames'.format(count))

    # Results
    print('\n  {:>16s}  {:>8s}  {:>8s}  {:>8s}'.format('', 'Avg(us)', 'Max(us)', 'Std(us)'))
    print('  {:<16s}  {:>8.0f}  {:>8.0f}  {:>8.1f}'.format(
        'Core 0 only:', solo_stats.mean, solo_stats.hi, solo_stats.std()))
    print('  {:<16s}  {:>8.0f}  {:>8.0f}  {:>8.1f}'.format(
        'Core 0+1:', dual_stats.mean, dual_stats.hi, dual_stats.std()))

    jitter_avg = dual_stats.mean - solo_stats.mean
    jitter_max = dual_stats.hi - solo_stats.hi
    print('\n  Jitter increase: {:+.0f} us avg, {:+.0f} us max'.format(jitter_avg, jitter_max))

    budget_us = 1_000_000 // config.SAMPLE_RATE_HZ
    if dual_stats.hi < budget_us:
        _ok('Within {} us budget'.format(budget_us))
    else:
        _warn('Max frame time ({:.0f} us) exceeds budget'.format(dual_stats.hi))

    print('  Core 1 alive: {:.0f} / {:.0f} seconds'.format(core1_alive_s, duration_ms / 1000))
    if core1_alive_s >= (duration_ms / 1000) - 2:
        _ok('Core 1 heartbeat stable')
    else:
        _warn('Core 1 heartbeat gaps detected')

    del k, k2, fsm, fsm2
    gc.collect()


# ── Test 7: Endurance Run ───────────────────────────────
def test_endurance():
    _header('Endurance Run — full pipeline stability (10 min)')
    print('  Ctrl+C to abort (partial results shown)\n')

    baro = _init_baro()
    power = _init_power()
    from sensors.barometer import pressure_to_altitude
    from flight.kalman import AltitudeKalman
    from flight.state_machine import FlightStateMachine
    from logging.datalog import FlightLogger, FRAME_FORMAT

    sd_ok = _init_sd()

    k = AltitudeKalman()
    fsm = FlightStateMachine()

    # Ground cal
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

    duration_s = 600  # 10 min
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

            # Flush SD periodically
            if sd_file is not None and total_frames % 25 == 0:
                try:
                    sd_file.flush()
                except OSError:
                    pass

            # Report
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

    # Close SD
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

    # Summary
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


# ── Test 8: Error Injection ─────────────────────────────
def test_error_injection():
    _header('Error Injection — fault tolerance verification')

    # Test A: I2C wrong address
    print('\n  A: I2C wrong address...')
    try:
        i2c = _init_i2c()
        try:
            i2c.readfrom_mem(0x50, 0x00, 1)
            _warn('No error from wrong address (device at 0x50?)')
        except OSError:
            # Good — now verify real sensor still works
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
        global _i2c, _baro
        from machine import SoftI2C, Pin
        # Destroy and recreate I2C
        _i2c = None
        _baro = None
        gc.collect()
        time.sleep_ms(50)
        _i2c = SoftI2C(sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL),
                       freq=config.I2C_FREQ, timeout=config.I2C_TIMEOUT_US)
        from sensors.barometer import BMP180
        _baro = BMP180(_i2c, config.BMP180_ADDR)
        p, t = _baro.read()
        _ok('Re-init success, BMP180 OK (P={:.0f} Pa)'.format(p))
    except Exception as e:
        _fail('Bus recovery failed: {}'.format(e))
        # Always try to restore
        try:
            _i2c = None
            _baro = None
            _init_baro()
        except Exception:
            pass

    # Test C: SD unmount/remount
    print('\n  C: SD unmount/remount...')
    if _init_sd():
        from logging.sdcard_mount import unmount, mount, is_mounted
        try:
            # Write before unmount
            with open('/sd/_diag_err_test.tmp', 'wb') as f:
                f.write(b'test')
            _ok('Write before unmount OK')

            # Unmount
            unmount()
            global _sd_mounted
            _sd_mounted = None

            # Try write (should fail)
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

            # Remount
            if mount():
                _sd_mounted = True
                try:
                    with open('/sd/_diag_err_test.tmp', 'wb') as f:
                        f.write(b'test2')
                    _ok('Remount + write OK')
                except Exception as e:
                    _fail('Write after remount failed: {}'.format(e))
            else:
                _fail('Remount failed')
                _sd_mounted = False

            # Cleanup
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


# ── Menu ────────────────────────────────────────────────
TESTS = [
    ('Sensor Bench', 'BMP180 timing + noise (1000 reads)', test_sensor_bench),
    ('SD Card Bench', 'Write/flush latency (5 min sustained)', test_sd_bench),
    ('Loop Budget', 'Per-stage pipeline timing (1000 frames)', test_loop_budget),
    ('RAM Profile', 'Memory usage + leak detection', test_ram_profile),
    ('Float Precision', 'Kalman drift over 10000 iterations', test_float_precision),
    ('Dual-Core Stress', 'Core 0+1 interference (60 sec)', test_dual_core),
    ('Endurance Run', 'Full pipeline stability (10 min)', test_endurance),
    ('Error Injection', 'Fault tolerance verification', test_error_injection),
]


def menu():
    from machine import freq as get_freq
    while True:
        print(CLEAR)
        print('{}{}  MPR ALTITUDE LOGGER — DIAGNOSTIC TUI{}'.format(BOLD, CYAN, RESET))
        print('  RP2040 @ {} MHz    fw v{}  diag v{}'.format(get_freq() // 1_000_000, config.VERSION, DIAG_VERSION))
        print()
        for i, (name, desc, _) in enumerate(TESTS):
            print('  {}. {:<20s} {}'.format(i + 1, name, desc))
        print('  0. Exit')
        print()

        try:
            sel = input('  Select [0-{}]: '.format(len(TESTS)))
        except (KeyboardInterrupt, EOFError):
            print('\n  Bye.')
            return

        try:
            n = int(sel.strip())
        except ValueError:
            continue

        if n == 0:
            print('  Bye.')
            return

        if 1 <= n <= len(TESTS):
            name, _, fn = TESTS[n - 1]
            try:
                fn()
            except KeyboardInterrupt:
                print('\n  [Aborted]')
            except Exception as e:
                _fail('Test crashed: {}'.format(e))

            print('\n  Press Enter to return to menu...', end='')
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass


# ── Auto-start (only when run as main script) ──
# When imported for individual test calls, menu() is NOT auto-run.
# To run interactively on Pico REPL: import pico_diag; pico_diag.menu()
