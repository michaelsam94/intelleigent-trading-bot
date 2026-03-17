#!/usr/bin/env bash
# Run after git pull: pull latest, then run full pipeline to signals (download → merge → features → labels → check balance → train → predict → signals).
#
# Usage: from project root
#   ./scripts/run_after_pull.sh
#   ./scripts/run_after_pull.sh --no-pull
#   ./scripts/run_after_pull.sh configs/config-1min-realtime.jsonc
#
# Before running: set "train": true in your config(s). After pipeline, set "train": false for the server.
# Requires: Python venv with deps; config(s) with data_folder, symbol, matrix_file_name, train: true.

set -e

# Project root (directory containing scripts/)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DO_PULL=true
CONFIGS=()

for arg in "$@"; do
  if [ "$arg" = "--no-pull" ]; then
    DO_PULL=false
  else
    CONFIGS+=("$arg")
  fi
done

if [ "$DO_PULL" = true ]; then
  echo "=== git pull ==="
  git pull
  echo ""
fi

if [ -f "venv/bin/activate" ]; then
  echo "=== activate venv ==="
  source venv/bin/activate
  echo ""
fi

echo "=== run pipeline to signals ==="
if [ ${#CONFIGS[@]} -eq 0 ]; then
  "$ROOT/scripts/run_pipeline_to_signals.sh"
else
  "$ROOT/scripts/run_pipeline_to_signals.sh" "${CONFIGS[@]}"
fi
