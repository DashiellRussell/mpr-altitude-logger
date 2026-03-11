# UNSW Rocketry — MPR Altitude Logger

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   RP2040 Dual Core                   │
│                                                     │
│  ┌─────────────────┐    ┌─────────────────────────┐ │
│  │     CORE 0      │    │        CORE 1           │ │
│  │  Sensor Loop     │───▶  Flight Computer        │ │
│  │  10-50 Hz        │    │  Kalman Filter          │ │
│  │  SD Card Logger  │    │  State Machine          │ │
│  │  Power Monitor   │    │  Apogee Detection       │ │
│  │  LED Status      │    │  Deployment Trigger     │ │
│  └─────────────────┘    └─────────────────────────┘ │
│           │                        │                 │
│           ▼                        ▼                 │
│  ┌─────────────┐          ┌──────────────┐          │
│  │  SD Card    │          │  Deploy GPIO │          │
│  │  8GB SPI    │          │  (e-match)   │          │
│  └─────────────┘          └──────────────┘          │
└─────────────────────────────────────────────────────┘
```

## Hardware Connections

| Component       | Interface | Pico Pins              |
|----------------|-----------|------------------------|
| BMP180 Baro    | I2C0      | SDA=GP4, SCL=GP5       |
| SD Card        | SPI1      | MISO=GP12, MOSI=GP11, SCK=GP10, CS=GP13 |
| V_BATT ADC     | ADC0      | GP26                   |
| V_5V ADC       | ADC1      | GP27                   |
| V_9V ADC       | ADC2      | GP28                   |
| Deploy Channel | GPIO      | GP16 (active HIGH)     |
| Status LED     | GPIO      | GP25 (onboard)         |
| Buzzer         | GPIO      | GP17                   |
| ARM Switch     | GPIO      | GP18 (pull-up, active LOW) |

## Flight States

```
PAD → BOOST → COAST → APOGEE → DROGUE → MAIN → LANDED
 │                      │
 └── ARM switch ────────┘ (deployment only if armed)
```

## Log Format

Binary frames at 25 Hz, each frame = 28 bytes:
- timestamp_ms   (u32)  — ms since boot
- state          (u8)   — flight state enum
- pressure_pa    (f32)  — raw barometer Pa
- temperature_c  (f32)  — barometer temp °C
- alt_raw_m      (f32)  — pressure-derived altitude
- alt_filtered_m (f32)  — Kalman-filtered altitude
- vel_filtered   (f32)  — Kalman-filtered vertical velocity m/s
- v_batt_mv      (u16)  — battery voltage
- flags          (u8)   — armed, deployed, error bits

Post-flight conversion: `python3 tools/decode_log.py flight.bin > flight.csv`

## First Boot — Hardware Check

Before loading the full flight computer, verify your board works:

1. Flash MicroPython onto Pico (v1.22+)
2. Copy `hw_check.py` to Pico as `main.py`
3. Open a serial monitor (Thonny / PuTTY / `screen /dev/ttyACM0 115200`)
4. Reboot — the script tests every component and reports PASS/FAIL
5. Fix any failures before proceeding

## Loading the Flight Computer

1. Copy the entire `avionics/` folder to Pico root
2. Copy `main.py` to Pico root (overwrites hw_check)
3. Ensure `sdcard.py` driver is on the Pico filesystem
4. Board boots into flight computer automatically
5. Flip ARM switch before launch (LED goes solid)
6. After recovery, pull SD card and run decoder

## OpenRocket Integration

Export your simulation from OpenRocket, then convert it for the review dashboard:

### Export from OpenRocket

1. Open your `.ork` file → Flight Simulations → Run simulation
2. Click **Plot / Export** → **Export data** tab
3. Select fields: Time, Altitude, Vertical velocity, Vertical acceleration,
   Mach number, Thrust, Drag force, Mass, Air pressure
4. ☑ Include flight events in comments
5. Separator: Comma → Export as `sim_export.csv`

### Convert to dashboard format

```bash
# Basic conversion
python tools/openrocket_import.py sim_export.csv -o sim_predicted.csv

# Also extract rocket params (mass, impulse, etc)
python tools/openrocket_import.py sim_export.csv --extract-params

# Inspect a .eng motor file
python tools/openrocket_import.py --eng-info path/to/motor.eng
```

### Or run the built-in sim (if you don't have OpenRocket)

```bash
python tools/simulate.py --mass 2.5 --motor Cesaroni_H100 --cd 0.45 --diameter 0.054
```

### Review the flight

```bash
# Decode actual flight data
python tools/decode_log.py flight.bin

# Open the dashboard (flight-review-dashboard.jsx)
# Upload flight.csv as "Actual Flight"
# Upload sim_predicted.csv as "Simulation"
# Compare tab shows deviation analysis
```
