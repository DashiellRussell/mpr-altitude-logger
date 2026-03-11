# TODO — MPR Altitude Logger

## Hardware

- [ ] **Fix 5V voltage divider** — R1∥R2 (500Ω) + R3 (1kΩ) puts 3.33V on ADC pin, saturating the 3.3V ADC. Replace R3 with **680Ω** for best resolution (V_tap=2.88V, ratio=1.735, 87% ADC range used). Then update `VDIV_5V` in `config.py` and `hw_check.py` to `1.735`.
  - Alternative: remove R1 or R2 entirely → 1k:1k divider, ratio stays 2.0, no software change needed.
- [ ] Verify 9V divider ratio matches schematic (currently reads 8.46V with ratio 3.0 — might be correct if input is ~8.5V)

## Software — Ground Station

- [ ] **Rewrite TUIs in Ink (React for CLI)** — replace Python/rich `preflight.py` and `postflight.py` with interactive Ink-based TUIs
  - Full-page dashboard layout
  - Tabs, scrollable views, better keyboard handling
  - Shared serial communication layer (Node.js + node-serialport)
- [ ] **Web dashboard** — browser-based flight review UI so team members can view data without CLI
  - Post-flight analysis with interactive charts (actual vs simulated overlay)
  - Could share components with Ink TUI
- [ ] Decide on monorepo structure for Ink TUI + web dashboard (e.g. `tools/ground-station/`)

## Software — Firmware

- [ ] Update `VDIV_5V` after resistor swap (currently 2.0, change to 1.735 if using 680Ω)
- [ ] Test deployment channel with ARM switch end-to-end
- [ ] Validate Kalman filter tuning with ground shake test
