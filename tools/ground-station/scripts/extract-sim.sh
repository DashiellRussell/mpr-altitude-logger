#!/usr/bin/env bash
# Extract simulation data from .ork files in sims/ directory
# Outputs sim_predicted.csv for use by the postflight TUI/web dashboard
#
# Usage: pnpm extract-sim
#        pnpm extract-sim sims/MPRrev7.ork
#        pnpm extract-sim sims/MPRrev7.ork --sim 2

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SIMS_DIR="$REPO_ROOT/sims"
IMPORTER="$REPO_ROOT/tools/openrocket_import.py"

if [ ! -f "$IMPORTER" ]; then
  echo "Error: openrocket_import.py not found at $IMPORTER"
  exit 1
fi

if [ $# -ge 1 ] && [ -f "$1" ]; then
  # Extract specific file
  ORK_FILE="$1"
  shift
  OUT_NAME="$(basename "${ORK_FILE%.*}")_sim.csv"
  OUT_PATH="$SIMS_DIR/$OUT_NAME"
  echo "Extracting: $ORK_FILE → $OUT_PATH"
  python3 "$IMPORTER" "$ORK_FILE" -o "$OUT_PATH" "$@"
else
  # Extract all .ork files in sims/
  if [ ! -d "$SIMS_DIR" ]; then
    echo "No sims/ directory found at $SIMS_DIR"
    exit 1
  fi

  count=0
  for ork in "$SIMS_DIR"/*.ork; do
    [ -f "$ork" ] || continue
    OUT_NAME="$(basename "${ork%.*}")_sim.csv"
    OUT_PATH="$SIMS_DIR/$OUT_NAME"
    echo "Extracting: $(basename "$ork") → $OUT_NAME"
    python3 "$IMPORTER" "$ork" -o "$OUT_PATH" || echo "  Warning: failed to extract $(basename "$ork")"
    count=$((count + 1))
  done

  if [ "$count" -eq 0 ]; then
    echo "No .ork files found in $SIMS_DIR/"
    exit 1
  fi

  echo ""
  echo "Done. $count sim(s) extracted to sims/"
  echo "The postflight dashboard will auto-discover these."
fi
