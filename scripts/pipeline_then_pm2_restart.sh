#!/usr/bin/env bash
# Run full pipeline (train → signals) then restart PM2 trading apps.
# Intended to be launched after each trade close when PIPELINE_ON_TRADE_CLOSE=1 (see notifier_trades).
#
# Env (optional):
#   PIPELINE_CONFIGS   Space-separated jsonc paths (default: 1min BTC + ETH)
#   PM2_RESTART_APPS   Comma-separated PM2 app names (default: all four servers)
#   PIPELINE_LOCK_DIR  Lock directory to avoid overlapping runs (default: /tmp/itb_pipeline_lock)
#   PIPELINE_AFTER_CLOSE_LOG  Log file (default: project logs/pipeline_after_close.log)
#
# Usage manually:
#   ./scripts/pipeline_then_pm2_restart.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCK_DIR="${PIPELINE_LOCK_DIR:-/tmp/itb_pipeline_lock}"
LOG="${PIPELINE_AFTER_CLOSE_LOG:-${ROOT}/logs/pipeline_after_close.log}"
mkdir -p "$(dirname "$LOG")"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Is) Skip: lock held ($LOCK_DIR) — pipeline already running" | tee -a "$LOG"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

exec >>"$LOG" 2>&1
echo ""
echo "=== $(date -Is) pipeline_then_pm2_restart START (pid=$$) ==="

if [ -n "${PIPELINE_CONFIGS:-}" ]; then
  # shellcheck disable=SC2206
  CONFIGS=($PIPELINE_CONFIGS)
else
  CONFIGS=(configs/config-1min-realtime.jsonc configs/config-1min-realtime-ethusdc.jsonc)
fi

echo "Configs: ${CONFIGS[*]}"
./scripts/run_pipeline_to_signals.sh "${CONFIGS[@]}"

if [ -n "${PM2_RESTART_APPS:-}" ]; then
  IFS=',' read -ra APPS <<< "$PM2_RESTART_APPS"
else
  APPS=(server-btcusdc server-btcusdc-5min server-ethusdc server-ethusdc-5min)
fi

echo "--- PM2 restart ---"
for app in "${APPS[@]}"; do
  app="${app//[$'\t\r\n']/}"
  app="${app// /}"
  [ -z "$app" ] && continue
  if command -v pm2 >/dev/null 2>&1; then
    if pm2 describe "$app" >/dev/null 2>&1; then
      echo "Restarting: $app"
      pm2 restart "$app" --update-env || echo "WARN: pm2 restart failed for $app"
    else
      echo "Skip (not in PM2): $app"
    fi
  else
    echo "WARN: pm2 not in PATH; skipping restarts"
    break
  fi
done

echo "=== $(date -Is) pipeline_then_pm2_restart DONE ==="
