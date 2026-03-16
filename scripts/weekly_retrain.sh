#!/usr/bin/env bash
# Weekly retrain: download → merge (train length) → features → labels → train.
# Use a config with "train": true, or temporarily set it for this run.
# Schedule with cron: 0 0 * * 0 /path/to/intelligent-trading-bot/scripts/weekly_retrain.sh
#
# Usage:
#   ./scripts/weekly_retrain.sh
#   ./scripts/weekly_retrain.sh configs/config-1min-realtime.jsonc
#   ./scripts/weekly_retrain.sh configs/config-1min-realtime.jsonc configs/config-1min-realtime-ethusdc.jsonc

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ $# -eq 0 ]; then
  CONFIGS=(configs/config-1min-realtime.jsonc configs/config-1min-realtime-ethusdc.jsonc)
else
  CONFIGS=("$@")
fi

for CONFIG in "${CONFIGS[@]}"; do
  echo ""
  echo "=== Weekly retrain: $CONFIG ==="
  python -m scripts.download -c "$CONFIG"
  python -m scripts.merge -c "$CONFIG" --train
  python -m scripts.features -c "$CONFIG"
  python -m scripts.labels -c "$CONFIG"
  python -m scripts.train -c "$CONFIG"
  echo "Done. Restart server to load new models (e.g. pm2 restart all)."
done

echo ""
echo "Weekly retrain finished for ${#CONFIGS[@]} config(s)."
