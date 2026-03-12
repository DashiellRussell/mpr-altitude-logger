# UNSW Rocketry — MPR Altitude Logger

Dual-core RP2040 avionics flight computer for AURC 2026. Logs barometric altitude, velocity, and power rail data at 25 Hz to SD card.

## Quick Start

### 1. Flash MicroPython

Flash MicroPython v1.22+ onto the Raspberry Pi Pico.

### 2. Hardware Check (first boot)

```bash
# Copy hw_check.py as main.py, reboot, check serial output
mpremote cp hw_check.py :main.py
mpremote reset
```

### 3. Load Flight Firmware

```bash
# Copy all avionics source files to Pico
mpremote cp main.py config.py :
mpremote mkdir sensors 2>/dev/null; mpremote cp sensors/barometer.py sensors/power.py :sensors/
mpremote mkdir flight 2>/dev/null; mpremote cp flight/kalman.py flight/state_machine.py :flight/
mpremote mkdir logging 2>/dev/null; mpremote cp logging/datalog.py logging/sdcard_mount.py :logging/
mpremote mkdir utils 2>/dev/null; mpremote cp utils/hardware.py :utils/
# Ensure sdcard.py driver is on the Pico filesystem
```

To update individual files after changes:
```bash
python3 -m mpremote connect /dev/cu.usbmodem11301 cp main.py :main.py
python3 -m mpremote connect /dev/cu.usbmodem11301 reset
```

### 4. Ground Station TUI

```bash
cd tools/ground-station
pnpm install    # first time only
pnpm build      # first time or after code changes
```

**Preflight check** — connects to Pico, runs hardware checks, shows live telemetry:
```bash
pnpm dev:tui -- preflight
# Or specify port:
pnpm dev:tui -- preflight --port /dev/cu.usbmodem1101
```

Keyboard shortcuts in preflight:
- `[B]` Boot Sequence — soft-resets Pico into main.py (requires GO status)
- `[R]` Recalibrate ground pressure
- `[T]` Re-run hardware checks
- `[G]` Manual GO override
- `[D]` Detailed hardware sub-checks
- `[Q]` Quit

**Postflight review** — decode binary flight log + charts:
```bash
pnpm dev:tui -- postflight flight.bin
pnpm dev:tui -- postflight flight.bin --sim sim_predicted.csv
```

**Web dashboard** — browser-based flight review:
```bash
pnpm dev:web
```

### 5. Flight Simulation

```bash
# From OpenRocket export
python tools/openrocket_import.py sim_export.csv -o sim_predicted.csv

# Standalone sim (no OpenRocket needed)
python tools/simulate.py --mass 2.5 --motor Cesaroni_H100 --cd 0.45 --diameter 0.054

# Inspect a .eng motor file
python tools/openrocket_import.py --eng-info path/to/motor.eng
```

### 6. Post-Flight Decode

```bash
# Decode binary log to CSV + matplotlib plots
python tools/decode_log.py flight.bin --plot

# Then load flight.csv + sim_predicted.csv into the web dashboard
```

## Architecture

```
Core 0 (time-critical, 25 Hz):
  Preflight checks → Sensor read → Kalman filter → State machine → SD card log

Core 1 (slower, ~20 Hz):
  LED status patterns (blink = running, solid = error)
```

Flight states: `PAD → BOOST → COAST → APOGEE → DROGUE → MAIN → LANDED`

## Hardware Connections

| Component       | Interface | Pico Pins              |
|----------------|-----------|------------------------|
| BMP180 Baro    | I2C       | SDA=GP4, SCL=GP5       |
| SD Card        | SPI0      | SCK=GP18, MOSI=GP19, MISO=GP16, CS=GP17 |
| 3V3 ADC        | ADC       | GP28                   |
| 5V ADC         | ADC       | GP26                   |
| 9V ADC         | ADC       | GP27                   |
| Status LED     | GPIO      | GP25 (onboard)         |

## Log Format

Binary frames at 25 Hz. Each frame = 2 sync bytes + 32 data bytes:

```
Sync:  \xAA\x55
Frame: u32 timestamp_ms | u8 state | f32 pressure_pa | f32 temperature_c |
       f32 alt_raw_m | f32 alt_filtered_m | f32 vel_filtered_ms |
       u16 v_3v3_mv | u16 v_5v_mv | u16 v_9v_mv | u8 flags
```

File header: `RKTLOG` (6B) + u16 version + u16 frame_size = 10 bytes.

## LED Guide

| Pattern            | Meaning                              |
|-------------------|--------------------------------------|
| Fast blink 250ms  | Preflight running                    |
| Slow blink 1s     | PAD — ready, safe to disconnect USB  |
| Solid ON          | Error (SD card lost, check serial)   |
| Fast blink 50ms   | BOOST detected                       |
| Medium blink      | COAST / descent                      |
| Double flash      | APOGEE                               |
| Triple flash      | LANDED — data saved                  |

## Repository Structure

```
avionics firmware (Pico):
  main.py              Entry point — dual-core orchestration
  config.py            Pin assignments, thresholds, tuning constants
  hw_check.py          Standalone first-boot hardware check
  ground_test.py       Pre-flight integration test
  sensors/             BMP180 driver, power rail monitor
  flight/              Kalman filter, state machine
  logging/             Binary frame logger, SD card mount
  utils/               LED status patterns

ground station (laptop):
  tools/ground-station/
    apps/tui/          Preflight + postflight TUI (Ink/React)
    apps/web/          Web dashboard (Vite + Recharts)
    packages/shared/   Binary decoder, analysis, shared types

python tools (laptop):
  tools/decode_log.py        Binary .bin → CSV + plots
  tools/simulate.py          1D Euler flight sim
  tools/openrocket_import.py OpenRocket CSV → dashboard format
```
