#!/usr/bin/env bash
# Run backtest (simulate) for both 5min configs: BTCUSDC and ETHUSDC.
#
# Prerequisite: signals.csv must exist for each symbol. If not, run the pipeline first:
#   1. Set "train": true in configs/config-5min-realtime.jsonc and configs/config-5min-realtime-ethusdc.jsonc
#   2. ./scripts/run_pipeline_to_signals.sh configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc
#   3. Set "train": false again in both configs
#
# Usage (from project root):
#   ./scripts/run_backtest_5min.sh              # backtest last N days (from config simulate_model.data_days, or all data if null)
#   ./scripts/run_backtest_5min.sh 14           # backtest last 14 days for both
#   ./scripts/run_backtest_5min.sh 14 --apply-best   # backtest then update configs with best thresholds
#
# Iterative train + backtest until target profit: python -m scripts.train_and_backtest_5min_until_optimum --max-iter 3 --target 0 --days 14
#
# Optional: add -i for interactive (prompt for balance, leverage, days).

set -e

DAYS=""
EXTRA=""
for arg in "$@"; do
  if [[ "$arg" =~ ^[0-9]+$ ]]; then
    DAYS="$arg"
  else
    EXTRA="$EXTRA $arg"
  fi
done

for CONFIG in configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc; do
  echo ""
  echo "=== Backtest: $CONFIG ==="
  if [ -n "$DAYS" ]; then
    python -m scripts.simulate -c "$CONFIG" -d "$DAYS" $EXTRA
  else
    python -m scripts.simulate -c "$CONFIG" $EXTRA
  fi
done

echo ""
echo "Done. Results in data/<SYMBOL>/signal_models.txt. Use --apply-best to write best params to config."
