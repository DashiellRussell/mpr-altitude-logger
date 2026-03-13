#!/usr/bin/env bash
# Deploy avionics firmware to Raspberry Pi Pico via mpremote.
# Usage: pnpm deploy:pico [--port /dev/tty.usbmodemXXXX]
#
# Copies all MicroPython source files from the repo root to the Pico filesystem.
# The TUI must NOT be running (it locks the serial port).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PORT_ARGS=""

# Parse optional --port argument
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT_ARGS="connect $2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: pnpm deploy:pico [--port /dev/tty.usbmodemXXXX]"
      exit 1
      ;;
  esac
done

echo "╔══════════════════════════════════════════╗"
echo "║   DEPLOY AVIONICS TO PICO               ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Repo: $REPO_ROOT"

# Check mpremote is available
if ! command -v mpremote &> /dev/null; then
  echo "ERROR: mpremote not found. Install with: pip install mpremote"
  exit 1
fi

# Files to deploy (relative to repo root)
FILES=(
  main.py
  config.py
  hw_check.py
  ground_test.py
)

# Directories to deploy (all .py files inside)
DIRS=(
  sensors
  flight
  logging
  utils
)

cd "$REPO_ROOT"

echo ""

# Step 1: Ensure directories exist on Pico (separate command to avoid mkdir errors)
echo "Ensuring directories exist on Pico..."
DIR_PY="import os"
for dir in "${DIRS[@]}"; do
  DIR_PY="$DIR_PY
try:
    os.mkdir('$dir')
    print('  Created $dir/')
except OSError:
    print('  $dir/ exists')"
done

mpremote $PORT_ARGS exec "$DIR_PY"

# Step 2: Copy all files in one chained mpremote command
echo ""
echo "Copying files..."

CMD="mpremote"
[ -n "$PORT_ARGS" ] && CMD="$CMD $PORT_ARGS"

for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    echo "  $f → :$f"
    CMD="$CMD + cp $f :$f"
  else
    echo "  SKIP $f (not found)"
  fi
done

for dir in "${DIRS[@]}"; do
  if [ -d "$dir" ]; then
    for f in "$dir"/*.py; do
      if [ -f "$f" ]; then
        echo "  $f → :$f"
        CMD="$CMD + cp $f :$f"
      fi
    done
  fi
done

eval $CMD

echo ""
# Show deployed version (macOS-compatible grep)
VERSION=$(grep 'VERSION' config.py | head -1 | sed 's/.*"\(.*\)".*/\1/' || echo "?")
echo "Deploy complete — v$VERSION"
echo "Power cycle or soft-reset the Pico to run."
