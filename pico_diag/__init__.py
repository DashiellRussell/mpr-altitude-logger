"""
Pico Diagnostic — on-device hardware stress testing.

Split into a package so each test file is small enough for
MicroPython's compiler (avoids MemoryError on large single files).

Run on Pico REPL: import pico_diag; pico_diag.menu()
From laptop TUI:  pnpm pico
"""

import gc
import config

DIAG_VERSION = "1.1.0"

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
        self.bins = [0] * (len(edges) + 1)

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
    from logging.sdcard_mount import mount, unmount, is_mounted
    if is_mounted():
        # Verify the existing mount is usable (not a stale leftover from
        # an interrupted main.py).  If listdir works, keep it; otherwise
        # unmount the stale VFS and remount fresh.
        try:
            import os
            os.listdir('/sd')
            _sd_mounted = True
            return True
        except OSError:
            pass
        # Stale mount — clean up before remounting
        unmount()
    _sd_mounted = mount()
    return _sd_mounted


# ── Test dispatch (lazy import — each test is a small file) ──

def test_sensor_bench():
    gc.collect()
    from pico_diag.t_sensor import run
    run()

def test_sd_bench():
    gc.collect()
    from pico_diag.t_sd import run
    run()

def test_loop_budget():
    gc.collect()
    from pico_diag.t_loop import run
    run()

def test_ram_profile():
    gc.collect()
    from pico_diag.t_ram import run
    run()

def test_float_precision():
    gc.collect()
    from pico_diag.t_float import run
    run()

def test_dual_core():
    gc.collect()
    from pico_diag.t_dual import run
    run()

def test_endurance():
    gc.collect()
    from pico_diag.t_endure import run
    run()

def test_error_injection():
    gc.collect()
    from pico_diag.t_error import run
    run()


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
