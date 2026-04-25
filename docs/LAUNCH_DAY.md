# Launch Day Ops — MPR Altitude Logger

Firmware target: **v2.0.0** (verify with `pnpm version`).
Run all commands from the project root: `~/Documents/mpr-altitude-logger`.

---

## The night before (do at home, on mains power)

1. **Pull latest + confirm firmware version**
   ```bash
   git status            # working tree should be clean — or know what's dirty
   pnpm version          # should print: Firmware version: 2.0.0
   ```
2. **Deploy firmware to the Pico** (USB cable, board powered from USB)
   ```bash
   pnpm deploy:pico
   ```
   Watch for `VERIFY_OK` at the bottom. If any module says `FAIL`, do NOT continue — fix on the bench.
3. **Smoke test through the preflight TUI** (do not skip this — memory rule: never deploy blind)
   ```bash
   pnpm preflight
   ```
   Confirm: I2C scan finds `0x77` (BMP180), all three rails green, baro pressure stable, RAM free > 50KB.
   - `[T]` re-runs hardware checks
   - `[D]` shows detailed sub-checks
   - `[Q]` quits
4. **Format/clear the SD card if it's getting full** — flights/ folder on the SD shows you've stacked many runs. Plenty of free space avoids any FAT slowdown.
5. **Battery check**
   - Fresh 9V alkaline (or known-good lithium). Memory pattern: weak 9V causes brown-out crashes during SD flush — non-negotiable.
   - Multimeter the 9V before connecting. Reject anything under ~9.0V loaded.
6. **Pack the launch kit** (see checklist at bottom).

---

## At the field — pre-arm bench check (15-30 min before pad)

Find a flat surface, laptop on USB. Goal: confirm the board is alive, calibrated, and the SD is mounted before it goes inside the airframe.

1. **Connect Pico via USB**, wait for `/dev/cu.usbmodem*` to appear.
2. **Run preflight TUI**
   ```bash
   pnpm preflight
   ```
   Verify each line is GREEN:
   - I2C device 0x77 present
   - BMP180 chip ID OK
   - Pressure ~ within ±5 hPa of weather report (sanity check the sensor)
   - 3V3 rail in 3.0–3.6 V
   - 5V rail in 4.5–5.5 V
   - 9V rail in 8.0–10.0 V — **if low, swap battery now**
   - SD mounted, free space > 50 MB
   - Frame size = 40 bytes (v3 log format)
3. **Press `[R]` to recalibrate ground pressure** at the pad's actual altitude. This sets AGL=0 at the launch elevation.
4. **Watch the live altitude trace for 10–15 seconds.** Standing still, the filtered altitude should be within ±1m and not drift. If you see the raw trace ramping or filtered alt diverging, recalibrate.
5. **Press `[B]` to boot into flight mode.** TUI exits cleanly; LED should go to slow blink (1s period) = PAD, ready, safe to disconnect.
6. **Disconnect USB.** Do NOT pull power yet — flight battery only from here on.

If anything was red or weird, run `pnpm pico:diag` for live diagnostics — it gives raw sensor reads, ADC values, frame timing without the checklist gating.

---

## At the pad

1. **Final visual** — LED slow-blinking (1s on/off). That confirms: preflight passed, in PAD state, logging armed, waiting for launch detection.
   - Solid ON = error → pull, debug back at the bench.
   - Fast 250ms blink = still booting. Wait. If it never settles to slow blink, something failed.
2. **Battery secure, no loose wires, SD card seated.** Vibration kills SD writes — physical integrity matters.
3. **Hand off to range crew.** No further laptop interaction needed; the board self-detects launch (alt > 15m AND vel > 10 m/s sustained 0.5s).

---

## What to look out for (red flags)

| Symptom | Cause | Action |
|---|---|---|
| Solid LED on the pad | preflight failed / fault | recover, USB in, run `pnpm preflight` to read state |
| Fast 250ms blink that never goes slow | stuck in preflight | power cycle; if persists, check SD card |
| 9V rail < 8.5V on TUI | weak/old battery | swap immediately — brown-out crashes mid-flush |
| Altitude drift > 2m on bench | I2C noise / loose baro | re-seat barometer cable, recalibrate |
| Frame timing > 30ms in pico_diag | SD card slow / SPI flaky | reformat SD, or swap card |
| TUI hangs on connect | another process holds the port | `lsof | grep usbmodem`, kill it (often a stale `mpremote`) |

---

## After flight — recovery

1. **Power off the avionics** before opening the bay if you can. Reduces chance of anyone yanking power mid-write (though we flush every 50 frames + state transition so the loss window is tiny).
2. **Pull the SD card.** Insert into laptop — should auto-mount as `/Volumes/AVIONICS/`.
3. **Run postflight straight off the SD** (memory rule: read from mounted SD, not local copies):
   ```bash
   pnpm postflight
   ```
   The TUI auto-detects the SD mount and lists flights newest first. Pick the one you just flew. It decodes, plots, and shows apogee/max-vel/durations.
4. **Copy the .bin to the repo for archival** (only after postflight read — keeps the SD canonical until you're sure):
   ```bash
   cp "/Volumes/AVIONICS/AVIONICS_flight_NNN.bin" flights/
   ```
5. **If anything looked anomalous in the summary**, decode the raw frames before trusting it (memory rule: don't trust summaries alone for anomalies):
   ```bash
   pnpm decode -- "flights/AVIONICS_flight_NNN.bin" --plot
   ```
6. **Check for crash reports.** A WDT_RESET will leave a crash blackbox in the next file's header — postflight surfaces it. If present, save the file and the boot log; investigate before the next flight.

---

## Multi-flight day — between flights

1. Disconnect avionics power, eject and reseat SD, swap or top up 9V.
2. USB → laptop → `pnpm preflight` → `[R]` recalibrate ground pressure → `[B]` boot.
3. Disconnect → into airframe.

Each flight gets its own auto-incremented filename (`AVIONICS_flight_NNN.bin`) — no overwrite risk.

---

## Quick command reference

```bash
pnpm version              # show firmware version
pnpm deploy:pico          # flash firmware to Pico (TUI must NOT be running)
pnpm preflight            # pad-side preflight + boot to flight mode
pnpm pico:diag            # raw live diagnostics (when preflight isn't enough)
pnpm postflight           # post-flight TUI, auto-detects SD
pnpm decode -- <file>     # raw binary → CSV + matplotlib
pnpm restate              # browse flights on SD, pick one
pnpm commands             # full command reference
```

If a port is locked: `lsof | grep usbmodem` → kill the holder.
If you need to specify the port: append `-- --port /dev/cu.usbmodemXXXX`.

---

## Launch kit checklist

- [ ] Laptop, charged, charger
- [ ] USB cable to Pico (data, not charge-only)
- [ ] 2× fresh 9V batteries (one spare)
- [ ] Multimeter
- [ ] Small phillips + flathead
- [ ] Spare SD card (formatted FAT32)
- [ ] SD card USB reader (in case avionics SD slot is uncooperative)
- [ ] Tape, zipties
- [ ] Printout of this doc (no field wifi guarantee)

---

## Emergency: avionics won't boot at the pad

Don't burn the launch slot trying to debug. Decision tree:
1. Solid LED → known-bad state. Pull, replace with backup if you have one, otherwise scratch the flight.
2. No LED at all → power. Check 9V, check switch, check connector.
3. Stuck fast-blink → power cycle once. If it repeats, it's failing preflight repeatedly — SD or baro. Swap SD first (cheapest, fastest).

When in doubt, scratch the flight. A scratched flight is recoverable; a bad log isn't.
