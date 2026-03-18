#!/usr/bin/env bash
# Reset local changes to tracked files, then pull. Use before pull to avoid conflicts.
# Keeps untracked files (e.g. data/). To remove untracked too, use: ./scripts/reset_and_pull.sh --clean
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CLEAN_UNTRACKED=false
for arg in "$@"; do
  if [ "$arg" = "--clean" ]; then
    CLEAN_UNTRACKED=true
  fi
done

echo "=== Reset local changes (tracked files) ==="
git restore .
# Or, to only reset configs: git restore configs/

if [ "$CLEAN_UNTRACKED" = true ]; then
  echo "=== Remove untracked files (e.g. data/) ==="
  git clean -fd
fi

echo "=== git pull ==="
git pull

echo "Done."
