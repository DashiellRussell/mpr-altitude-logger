const fs = require("fs");
const path = require("path");

module.exports = function help() {
  // Read firmware version from config.py
  let version = "unknown";
  try {
    const config = fs.readFileSync(
      path.join(__dirname, "..", "config.py"),
      "utf8"
    );
    const match = config.match(/VERSION\s*=\s*"(.+?)"/);
    if (match) version = match[1];
  } catch {}

  const text = `
\x1b[1;36m MPR Altitude Logger \x1b[0m  firmware v${version}
\x1b[2m UNSW Rocketry — RP2040 Avionics Flight Computer\x1b[0m

\x1b[1;33m CRITICAL — Launch Day\x1b[0m                          \x1b[2mrun from: project root\x1b[0m
  pnpm preflight         Pre-flight check suite
  pnpm postflight        Post-flight log decode + analysis
  pnpm deploy:pico       Deploy firmware to Pico via USB

\x1b[1;32m Development\x1b[0m                                     \x1b[2mrun from: project root\x1b[0m
  pnpm dev:tui           Launch TUI dashboard
  pnpm dev:web           Start web dashboard (Vite)        \x1b[2m→ localhost:5173\x1b[0m
  pnpm simulator         Flight simulator TUI
  pnpm pico:diag         Pico diagnostics TUI (live USB)

\x1b[1;32m Analysis\x1b[0m                                        \x1b[2mrun from: project root\x1b[0m
  pnpm decode -- <file>  Decode binary .bin log to CSV
  pnpm restate           Restate TUI (auto-detect SD, select flight)
  pnpm postflight        Full post-flight pipeline

\x1b[1;32m Testing\x1b[0m                                         \x1b[2mrun from: project root\x1b[0m
  pnpm test              Run all unit tests
  pnpm test:integration  Run integration tests only

\x1b[1;32m Info\x1b[0m
  pnpm version           Show firmware version (config.py)
  pnpm commands          Show this message

\x1b[1;32m Ground Station\x1b[0m                                  \x1b[2mrun from: tools/ground-station/\x1b[0m
  pnpm dev:tui           TUI dashboard
  pnpm dev:web           Web dashboard
  pnpm build             Build all packages
  pnpm seed              Seed test flight data
  pnpm seed:sd           Seed flight data to SD card

\x1b[1;32m Standalone Tools\x1b[0m                                \x1b[2mrun directly with python3\x1b[0m
  python3 tools/decode_log.py <file> --plot
  python3 tools/simulate.py --mass 2.5 --motor <motor>
  python3 tools/openrocket_import.py <csv> -o sim.csv
`;

  console.log(text);
};
