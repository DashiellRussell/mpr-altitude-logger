# Pre-Launch Procedures

**Vehicle:** Hobby rocket
**Avionics firmware:** v2.0.0 (MPR Altitude Logger)

## Conventions

- **T-N** = N days before launch day. T-0 = launch day at the field.
- **GO / NO-GO** lines at the end of each phase are mandatory checkpoints — if any condition fails, do not proceed without the relevant lead's sign-off.
- All `pnpm` commands run from `~/Documents/mpr-altitude-logger` on Dash's laptop.

## Abbreviations

- **ORK** — OpenRocket simulation file
- **lwr** — lower (bulkhead)
- **AGL** — altitude above ground level
- **WDT** — watchdog timer (reboot if firmware hangs)
- **SD** — SD card (microSD on the avionics PCB)
- **TUI** — text-based UI on the laptop (preflight / postflight tools)

---

# Aerostructures

## T-1

Check wind predictions for the day

- Pull forecast for the launch site, hour-by-hour for the launch window
- Conduct additional ORK simulations at predicted windspeed ± 20%
- Calculate appropriate ballast for windspeed at predicted ± 50%
- Record the ballast mass for each scenario in the team sheet

## T-0 (Launch Day)

Install eyebolt in lower bulkhead

- Drill as close to the centre as possible
- Use the pre-marked drill jig if available
- Confirm eyebolt threads engage cleanly before epoxy step

Motor integration

- Friction-fit the motor into the airframe
- Use the motor retention clip to retain the motor
- Confirm clip is fully seated — no visible gap

Measure wind

- Use the handheld anemometer (the "fan-lookin' thing")
- Record three readings 30 seconds apart at pad height
- If wind exceeds the launch limit from your ORK sims, hold

Add ballast to the tip of the nose cone

- Mix up some 5-minute epoxy
- Log weight of mixed epoxy
- Measure split shot weight: `W_ss = W_ballast - W_epoxy`
- Place split shot sinkers at the tip of the nose cone, then add the epoxy
- Hold the nosecone nose-down until the epoxy cures (~5 min, verify with toothpick)

Add battery (coordinate with Avionics — battery only goes in AFTER avionics bench check passes)

- Connect battery to the flight computer
- Battery life on fresh 9V alkaline: ~4+ hours idle. Install within 1 hour of launch slot. Set a phone timer.
- Lower battery into the slot in the nose cone
- Secure battery: electrical tape preferred. Hot glue is a fallback only — accept that it may shift under boost loads.

Install flight computer

- Lower the connected PCB into the nosecone
- Use 3× M3 magnetic screws + screwdriver to screw the PCB in place
- DO NOT overtighten — PLA threads strip easily
- Verify slow-blink LED is still visible through the nosecone vent / pressure hole before sealing
- Note: the lower bulkhead (next step) is epoxied. Once cured, the only way to access the PCB screws is to cut the nosecone open at post-flight.

Install lower bulkhead

- Mix up 5-minute epoxy
- Lather the sides and rear of the bulkhead with epoxy
- Using the eyebolt, hold the bulkhead in place until epoxy cures
- Tie the shock cord to the eyebolt

NOSECONE DONE!

Integrate nosecone with main airframe

Drill pressure hole

- Take care not to drill into the avionics bay or shock cord
- Hole diameter per design spec — confirm before drilling

**GO / NO-GO (Aerostructures):**

- GO: ballast logged, motor retained, bulkhead cured, pressure hole drilled, avionics LED slow-blinking through pressure hole
- NO-GO: any epoxy uncured, motor not fully seated, avionics LED solid or absent

STRUCTURES DONE

---

# Avionics

## T-1

Firmware deploy (Dash's laptop, board on USB)

- `cd ~/Documents/mpr-altitude-logger`
- `git status` — working tree should be clean, or you know exactly what's dirty
- `pnpm version` — must print `Firmware version: 2.0.0`
- `pnpm deploy:pico` — wait for `VERIFY_OK` at the bottom
- If any module reports `FAIL`, STOP — fix on the bench, do not bring a half-deployed board to the field

Bench preflight (smoke test on home network/desk before packing)

- `pnpm preflight`
- Confirm all rails green, I2C 0x77 present, BMP180 chip ID OK, RAM free > 50 KB
- Press `[Q]` to quit cleanly

10-minute logging stress test (T-1 only — too slow for launch day)

The board flies for less than a minute, but a degraded SD or marginal timing only shows up over many minutes. Run this once the night before.

- `pnpm preflight` → `[R]` recalibrate → `[B]` boot to PAD
- Disconnect USB. Power the board from a 9V (or leave on USB if you trust the host won't sleep) for at least 10 minutes
- LED should be slow-blink (PAD) the whole time. If it ever goes solid, abort the test and investigate.
- After 10 min, power off, pull SD, run `pnpm decode -- "/Volumes/AVIONICS/AVIONICS_flight_NNN.bin" --plot`
- Verify, at 50 Hz sample rate:
  - Frame count ≈ 30,000 ± 1% over 600 s (i.e. 29,700–30,300 frames)
  - Mean sample interval = 20 ms ± 1 ms
  - No timing gap > 50 ms (one missed frame is suspicious; multiple = bad)
  - No `[CRASH REBOOT]` line in the boot log on next power-up
  - File grew monotonically (no truncation), no `\xAA\x55` resync gaps in the middle
- If frame count is short, intervals are off, or there are gaps, do NOT fly — swap SD card and re-run. If a fresh SD also fails, investigate firmware before bringing the board to the field.

SD card prep

- Confirm SD seated in the avionics PCB
- FAT32 formatted, > 50 MB free (check the existing flights/ folder isn't full)
- Spare SD card formatted FAT32 and packed

Battery prep

- 2× fresh 9V alkaline (one flight, one spare)
- Unloaded multimeter check — reject anything under 9.0 V
- Tape over terminals until install

## T-0 (Launch Day)

Bench preflight at the field (do this BEFORE Aerostructures starts nosecone integration)

- Connect Pico to laptop via USB. Wait for `/dev/cu.usbmodem*` to appear.
- `cd ~/Documents/mpr-altitude-logger`
- `pnpm preflight`
- Verify EVERY line is green:
  - I2C scan finds device 0x77
  - BMP180 chip ID OK
  - Pressure within ±5 hPa of weather report (sanity-check the sensor)
  - 3V3 rail in 3.0–3.6 V
  - 5V rail in 4.5–5.5 V
  - 9V rail in 8.0–10.0 V — if low, swap battery now
  - SD mounted, free space > 50 MB
  - Frame size = 40 bytes (v3 log format)
- If anything is red, run `pnpm pico:diag` for raw live diagnostics

Calibrate and arm

- Press `[R]` to recalibrate ground pressure at pad elevation
- Watch the live altitude trace for 10–15 seconds standing still
- Filtered altitude must hold within ±1 m and not drift. If it drifts, recalibrate. If it still drifts, abort to NO-GO.
- Press `[B]` to boot into flight mode
- TUI exits cleanly. LED transitions to slow blink (1 s on / 1 s off) = PAD state, armed, logging-ready
- Disconnect USB. Do NOT pull power yet — flight battery only from here on

Hand off to Aerostructures

- Coordinate the "Add battery" → "Install flight computer" → "Install lower bulkhead" sequence
- Battery should be installed within 1 hour of expected launch slot — phone timer mandatory
- After battery is connected and PCB installed, confirm slow-blink LED is visible through the nosecone vent before Aerostructures closes the bulkhead

Final pad check (just before vertical)

- Visually confirm slow-blink LED through the pressure hole / vent
- Slow blink only = GO. Anything else = NO-GO
- No laptop interaction at the pad. The board self-detects launch (alt > 15 m AND velocity > 10 m/s, sustained 0.5 s)

LED reference (memorise this)

- Fast blink (250 ms) — booting / preflight in progress
- Solid ON — error
- **Slow blink (1 s) — PAD, ready, safe to fly**
- Fast blink (50 ms) — BOOST detected
- Medium blink — COAST / DROGUE / MAIN descent
- Double flash — APOGEE
- Triple flash — LANDED, data safe

Post-flight (after recovery)

The avionics PCB is held by 3× M3 screws, but the lower bulkhead is epoxied — so the bay is sealed and the screws cannot be reached without cutting the nosecone open. Power is still live until the battery is unplugged — treat the board as energised.

Cut the nosecone

- Choose ONE cut location:
  - Option A — circumferential cut **just below the avionics** (between PCB and the lower bulkhead). Exposes the battery side first.
  - Option B — circumferential cut **just above the avionics** (between PCB and the nose tip / ballast). Exposes the PCB top side first.
- Mark the cut line with a pencil all the way around before cutting
- Use a hand saw, rotary tool, or sharp knife — slow and steady, multiple shallow passes
- WARNING: stop cutting the moment you feel the wall give. Do NOT push the blade past the wall thickness — the board, battery, or wiring is directly behind it.
- Pause and inspect every few mm. Pull debris out as you go.

Disconnect the battery FIRST

- Once the cut is open, locate the battery clip and disconnect it before doing anything else
- LED should go dark — confirms power is off
- Tape over the 9V terminals immediately

Free the PCB and SD

- Brush or blow swarf out of the bay before unscrewing
- Reach through the cut and unscrew the 3× M3 PCB screws
- Catch each screw as it comes out — if one drops into the bay it can roll under the PCB and short it. Account for all 3.
- WARNING: do NOT let a screw, screwdriver tip, blade, or piece of swarf touch the board while power is live or after — debris bridging pads can short the board and brick it.
- Lift the PCB out cleanly, supporting it by the edges (not by components)
- Pull the SD card out cleanly

Decode the flight

- Insert the SD card into laptop — auto-mounts as `/Volumes/AVIONICS/`
- `pnpm postflight` — TUI auto-detects SD, lists flights newest first, decodes selected flight
- Save the `.bin`, `.csv`, and `_report.txt` to the `flights/` folder
- If summary looks anomalous: `pnpm decode -- "flights/AVIONICS_flight_NNN.bin" --plot` to inspect raw frames
- If a `[CRASH REBOOT]` line appears in the boot log, save the file and flag it before the next flight — do not reuse the SD until investigated

Multi-flight day — between flights

- Disconnect avionics power, eject and reseat SD, swap or top up 9V
- USB → laptop → `pnpm preflight` → `[R]` recalibrate → `[B]` boot
- Disconnect → into airframe
- Each flight gets an auto-incremented filename (`AVIONICS_flight_NNN.bin`) — no overwrite risk

**GO / NO-GO (Avionics):**

- GO: all preflight rails green, ground pressure calibrated, slow-blink LED on PAD, SD mounted with > 50 MB free
- NO-GO (any of these = scratch the flight):
  - Solid LED at any point after preflight
  - 9V rail < 8.5 V on TUI
  - Altitude drift > 2 m on bench
  - SD won't mount or < 10 MB free
  - Frame timing > 30 ms in pico_diag
  - Stuck fast-blink LED that won't settle to slow blink within 30 s of boot

Emergency: avionics won't boot at the pad

1. Solid LED → known-bad state. No spare PCB — scratch the flight.
2. No LED at all → power. Check 9V, switch, connector.
3. Stuck fast-blink → power cycle once. If it repeats, swap SD card first (cheapest, fastest fix).

When in doubt, scratch. A scratched flight is recoverable; a corrupt log is not.

AVIONICS DONE!

---

# Recovery

**Deployment:** chute is deployed by the motor's delay charge. No separate pyro / e-match / electronic deployment. The motor's delay grain ejects the chute via the forward closure when the propellant burns through.

## T-1

Chute inspection

- Unfold chute fully, lay flat
- Check canopy for tears, scorch marks, melted shroud lines
- Check shock cord for fraying, knots holding firm
- Check swivels and quick links for damage
- Repack chute loosely (final pack happens T-0)

## T-0 (Launch Day)

Chute pack and install

- Fold chute per team's standard fold
- Wrap in nomex or chute protector to shield from motor exhaust gases
- Connect to shock cord, shock cord to eyebolt on lower bulkhead
- Pack into airframe, leaving room above for ejection
- Confirm motor delay matches predicted apogee time from ORK sim — wrong delay = destroyed rocket

WARNING: motor delay must match predicted coast time. Wrong delay → chute deploys under high speed and shreds (too short), or rocket lawn-darts (too long). Check twice before motor install.

**GO / NO-GO (Recovery):**
- GO: chute inspected, packed, shock cord connected, motor delay matches sim (±1 s)
- NO-GO: damaged canopy, frayed shock cord, motor delay mismatch

## Field Recovery (post-launch)

Watch and track

- Eyes on the rocket from launch to landing — assign a designated tracker
- Note apogee location and chute deployment (visible puff)
- Track descent direction, note landmarks near landing zone
- Wait for Range Director's clearance before walking onto the field

Walk-out

- Recovery team: 2+ people, hi-vis vests, phone with GPS, radio if available
- Don't cross active landing zones for other rockets until cleared
- Photograph the landing site before touching

Approach the rocket

- Spent motor case may still be hot — give it a few minutes if you arrive fast
- Inspect for damage: bent fins, cracked airframe, tangled chute, missing nosecone
- Check chute condition for post-flight notes

Avionics handling in the field

The avionics is still powered. The board's last act is to detect LANDED and flush the SD — the LED tells you whether that worked.

- Look at the LED through the nosecone vent / pressure hole:
  - Triple flash = LANDED, data flushed, safe to transport. Best case.
  - Slow blink = still in PAD — launch was never detected, log will be short or empty
  - Other pattern = stuck in a mid-flight state, may not have flushed cleanly
- DO NOT disconnect the battery in the field unless you have to. Let the board sit at LANDED so any pending writes finish.
- If the LED is in an odd state, photograph it before moving — useful for post-flight diagnosis

WARNING: vibration during SD writes can corrupt the last few frames. If the board is still mid-flush (not yet triple flash), avoid jostling the rocket. Walking back is fine; throwing it in the car is not.

Transport back to bench

- Carry the rocket supported, not by the nosecone
- Battery stays connected. If transport > 30 min AND LED is triple flash (LANDED), you can power off — data is safe.
- Bag the rocket if it's wet, muddy, or going through dust
- At the bench, hand off to Avionics for the post-flight cut-out

If the rocket is lost / unrecoverable

- Mark last seen position on a map (phone GPS pin)
- Search in expanding circles from predicted landing zone
- Ask other teams if they spotted it
- If unrecoverable: SD card and battery are in the rocket, data is gone. Note for post-mortem.

RECOVERY DONE

---

# Propulsions

*[TO FILL IN — owner: ____]*

Suggested structure:
- T-1: motor inspection, igniter inventory, certification paperwork
- T-0: motor install (coordinate with Aerostructures), igniter install, continuity check, GO/NO-GO criteria
- Post-flight: motor case retrieval, post-fire inspection

---

# Master Timeline (T-0)

Approximate, adjust to launch slot:

| Time | Owner | Action |
|---|---|---|
| Slot - 3:00 h | All | Arrive, unpack, set up bench |
| Slot - 2:30 h | Aerostructures | Eyebolt, motor friction fit, ballast prep |
| Slot - 2:00 h | Avionics | Bench preflight, calibrate, boot to PAD |
| Slot - 1:30 h | Avionics + Aerostructures | Battery install, PCB install in nosecone |
| Slot - 1:15 h | Aerostructures | Lower bulkhead epoxy, nosecone integration |
| Slot - 0:45 h | Recovery + Propulsions | Charges, igniter, final continuity |
| Slot - 0:30 h | All | Walk to pad, vertical, final visual checks |
| Slot - 0:10 h | Range | Range crew takeover |
| Slot 0:00 | — | LAUNCH |
| Slot + 0:30 h | All | Recovery walk |
| Slot + 1:00 h | Avionics | Pull SD, postflight decode |

## Comms

- **Launch Director** calls GO/NO-GO at each phase. Anyone can call NO-GO at any time for any reason.
- Subsystem leads must verbally confirm GO at handoff points (Avionics → Aerostructures battery install, etc.)

---

# Pack List

## Structures

- Spare nosecone
- 5-minute epoxy (lower bulkhead)
- 3× M3 magnetic screws (avionics PCB) + spares
- Magnetic screwdriver (M3 bit)
- Split shot sinkers
- Eye bolt
- Drill + drill bits (eyebolt + pressure hole)
- Toothpicks (epoxy cure check)
- Electrical tape, hot glue gun + sticks (fallback)
- Hand saw / Dremel / sharp knife (for post-flight nosecone cut)
- Bench vice or clamp (to hold nosecone steady during cut)
- Safety glasses

## Avionics

- Laptop + charger (Dash)
- USB-A to micro-USB cable — DATA capable, not charge-only
- 2× fresh 9V batteries (alkaline)
- Multimeter
- Spare SD card, formatted FAT32
- USB SD card reader
- Printout of this doc (no field wifi guaranteed)

## Recovery

- Parachute (main + drogue if applicable)
- Shock cord, swivels
- Ejection charges (per manifest)
- Igniter / e-match wire
- Continuity tester
- Nomex / chute protector
- Spare quick links

## Propulsions

- Motor (per manifest)
- Spare igniter(s)
- Motor retention clip
- Cert paperwork
- Igniter wire
- Continuity tester (shared with Recovery OK)

## Shared

- First aid kit
- Hi-vis vests
- Sunscreen, water, snacks
- Folding table, chairs
- Power bank for laptop
