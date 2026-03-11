#!/usr/bin/env node
import { render } from 'ink';
import React from 'react';
import { App } from './app.js';

function parseArgs(argv: string[]): {
  port?: string;
  mode: 'preflight' | 'postflight';
  binFile?: string;
  simFile?: string;
} {
  const args = argv.slice(2);
  let port: string | undefined;
  let mode: 'preflight' | 'postflight' | undefined;
  let binFile: string | undefined;
  let simFile: string | undefined;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--port' && i + 1 < args.length) {
      port = args[++i];
    } else if (arg === '--mode' && i + 1 < args.length) {
      const m = args[++i];
      if (m === 'preflight' || m === 'postflight') {
        mode = m;
      } else {
        process.stderr.write(`Unknown mode: ${m}. Use 'preflight' or 'postflight'.\n`);
        process.exit(1);
      }
    } else if (arg === '--sim' && i + 1 < args.length) {
      simFile = args[++i];
    } else if (arg === '--help' || arg === '-h') {
      process.stdout.write(
        `Usage: mpr-tui [options] [file.bin]

Options:
  --port <path>          Serial port (auto-detect if omitted)
  --mode <mode>          preflight | postflight
  --sim <file.csv>       Simulation CSV for comparison (postflight)
  -h, --help             Show this help

Examples:
  mpr-tui                           Pre-flight check (auto-detect Pico)
  mpr-tui --port /dev/cu.usbmodem1  Pre-flight on specific port
  mpr-tui flight.bin                Post-flight analysis
  mpr-tui flight.bin --sim sim.csv  Post-flight with sim overlay
`
      );
      process.exit(0);
    } else if (!arg.startsWith('--')) {
      binFile = arg;
    }
  }

  // Determine mode from context if not explicit
  if (!mode) {
    if (binFile) {
      mode = 'postflight';
    } else {
      mode = 'preflight';
    }
  }

  return { port, mode, binFile, simFile };
}

const config = parseArgs(process.argv);

render(
  <App
    mode={config.mode}
    port={config.port}
    binFile={config.binFile}
    simFile={config.simFile}
  />
);
