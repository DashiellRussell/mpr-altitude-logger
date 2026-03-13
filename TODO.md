# TODO — MPR Altitude Logger

## Hardware

- [x] **Fix 5V voltage divider** — R3 replaced with 680Ω. `VDIV_5V` updated to `1.735` in `config.py` and `hw_check.py`.
- [ ] Verify 9V divider ratio matches schematic (currently reads 8.46V with ratio 3.0 — might be correct if input is ~8.5V)

## Software — Ground Station

- [x] **Rewrite TUIs in Ink (React for CLI)** — replace Python/rich `preflight.py` and `postflight.py` with interactive Ink-based TUIs
  - Full-page dashboard layout
  - Tabs, scrollable views, better keyboard handling
  - Shared serial communication layer (Node.js + node-serialport)
- [x] **Web dashboard** — browser-based flight review UI so team members can view data without CLI
  - Post-flight analysis with interactive charts (actual vs simulated overlay)
  - Could share components with Ink TUI
- [x] Decide on monorepo structure for Ink TUI + web dashboard (e.g. `tools/ground-station/`)
- [ ] **Boot sequence TUI** — press [B] in preflight to soft-reset Pico into main.py and watch boot steps stream in
  - [x] PicoLink passthrough mode (line-reading after soft reset)
  - [x] Boot step parser ([1/7]...[7/7], [RDY], [PAD] telemetry)
  - [x] LED indicator component (blink/solid-error/solid-ready)
  - [x] Two-press [B] confirmation, GO-gated
  - [ ] Remove debug diagnostics (lineBuffer dump, byte counters) once boot sequence is stable
  - [ ] Test with actual flight firmware on Pico (all avionics source files must be on the Pico filesystem)
  - [ ] Handle `machine.soft_reset()` USB re-enumeration on different MicroPython builds (currently uses Ctrl-B → type command at >>> prompt)
  - [ ] Verify main.py boot output is parseable when SD card or barometer fails (FATAL path, countdown path)

## Software — Firmware

- [x] Update `VDIV_5V` after resistor swap (changed to 1.735)
- [ ] Test deployment channel with ARM switch end-to-end
- [ ] Validate Kalman filter tuning with ground shake test
