# MPR Ground Station

Ground station tooling for the MPR Altitude Logger — UNSW Rocketry.

pnpm monorepo with a terminal UI (Ink/React), web dashboard, and shared flight data library.

## Quick Start

```bash
cd tools/ground-station
pnpm install
pnpm build
```

## Commands

All commands run from `tools/ground-station/`.

### Development

| Command | Description |
|---|---|
| `pnpm dev:tui -- preflight` | Launch preflight check TUI (auto-detects Pico) |
| `pnpm dev:tui -- postflight` | Launch post-flight analysis TUI (auto-detects SD card) |
| `pnpm dev:tui -- postflight <file.bin>` | Post-flight with a specific .bin file |
| `pnpm dev:web` | Launch web dashboard (Vite dev server) |
| `pnpm build` | Build all packages |
| `pnpm test` | Run all tests |
| `pnpm test:shared` | Run shared library tests only |

### Deployment

| Command | Description |
|---|---|
| `pnpm deploy:pico` | Deploy firmware to Pico via mpremote |
| `pnpm deploy:pico -- --port /dev/cu.usbmodem1` | Deploy on a specific serial port |

### Flight Data

| Command | Description |
|---|---|
| `pnpm seed` | Generate synthetic flight log (`seed_flight/flight.bin`) |
| `pnpm seed:sd` | Generate and write directly to mounted SD card |
| `pnpm seed:verify` | Generate + decode round-trip verification |
| `pnpm seed -- --motor Estes_E12 --mass 0.8` | Custom rocket parameters |
| `pnpm extract-sim` | Extract simulation CSV from OpenRocket export |

## Monorepo Structure

```
tools/ground-station/
  apps/
    tui/          Ink (React) terminal UI — preflight checks + post-flight analysis
    web/          Vite + React web dashboard for flight review
  packages/
    shared/       Flight data decoder, analysis, CSV export, report generator
  scripts/
    deploy-pico.sh    Deploy firmware files to Pico via mpremote
    extract-sim.sh    Extract sim CSV from OpenRocket .ork exports
```

## Preflight TUI

Interactive terminal dashboard for pre-launch hardware checks.

- Auto-detects Pico on USB serial
- Runs I2C scan, barometer, SD card, ADC, and LED checks
- Live telemetry view with voltage monitoring
- Boot sequence with retry support
- `pnpm dev:tui -- preflight`

## Post-flight TUI

Terminal dashboard for analysing flight logs after recovery.

- Auto-discovers .bin files on mounted SD cards
- Decodes binary frames and displays flight summary
- ASCII altitude chart with state-coloured regions
- State timeline and velocity sparkline
- Scrollable detailed log viewer (L key) with red voltage warnings
- Auto-generates .csv and _report.txt alongside the .bin
- Export to Desktop as .zip (D key) with .bin, .csv, report, and preflight metadata
- `pnpm dev:tui -- postflight`

### Post-flight Keybindings

| Key | Action |
|---|---|
| L | Detailed log viewer (scrollable frame table) |
| D | Export .zip to ~/Desktop |
| E | Export .csv |
| S | Save full flight report (.txt) |
| Q | Quit |

## Seed Flight Generator

Generates realistic synthetic flight data for testing the post-flight pipeline without needing a real launch.

Uses the physics simulator to produce a flight profile, adds sensor noise, runs a Kalman filter, and writes the result in the on-board binary format.

```bash
pnpm seed                                    # default: H100 motor, 2.5kg
pnpm seed -- --motor Estes_E12 --mass 0.8    # smaller rocket
pnpm seed:sd                                 # write to SD card
pnpm seed:verify                             # generate + verify decode
```

Output: `seed_flight/flight.bin` + `seed_flight/preflight.txt` (per-flight folder format matching the on-board logger).

## SD Card Layout

The on-board logger writes per-flight folders:

```
/sd/
  flight_001/
    flight.bin       Binary telemetry frames (32 bytes each, 25 Hz)
    preflight.txt    Preflight check results and metadata
  flight_002/
    flight.bin
    preflight.txt
```

## Flight Report

Loading a flight auto-generates a `_report.txt` containing:

- Flight summary (apogee, max velocity, acceleration, landing velocity)
- ASCII rocket art with key stats
- Altitude vs time chart (state-coloured)
- State timeline bar and transition list
- Power rail ranges
- Full frame log table (every frame, all fields)
