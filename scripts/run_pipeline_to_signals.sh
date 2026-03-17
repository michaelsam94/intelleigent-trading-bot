#!/usr/bin/env bash
# Run the full pipeline so that predictions.csv and signals.csv exist for backtesting.
# Usage: from project root:
#   ./scripts/run_pipeline_to_signals.sh                    # run both BTCUSDC and ETHUSDC
#   ./scripts/run_pipeline_to_signals.sh <config.jsonc>      # run one config
#   ./scripts/run_pipeline_to_signals.sh <config1> <config2> # run multiple configs
#
# Required: set "train": true in the config before running (step 5 trains lc, gb, xgb, meta).
# After pipeline completes, set "train": false again for the server so it doesn't retrain on every run.
# If you skip this, XGB and trade_score_meta models will be missing and the server will log "Cannot load model... Skip."

set -e

if [ $# -eq 0 ]; then
  CONFIGS=(configs/config-1min-realtime.jsonc configs/config-1min-realtime-ethusdc.jsonc)
else
  CONFIGS=("$@")
fi

for CONFIG in "${CONFIGS[@]}"; do
  echo ""
  echo "=== Pipeline to signals (config: $CONFIG) ==="

  # Ensure data folder exists (download creates symbol subdir; merge needs data_folder)
  DATA_DIR=$(python -c "
import re, json, sys
with open(sys.argv[1]) as f: s = re.sub(r'//.*', '', f.read())
print(json.loads(s).get('data_folder', './data'))
" "$CONFIG")
  mkdir -p "$DATA_DIR"
  echo "Data folder: $DATA_DIR"

  echo "1/7 Download..."
  python -m scripts.download -c "$CONFIG"

  echo "2/7 Merge (--train for train_length bars, e.g. 120k)..."
  python -m scripts.merge -c "$CONFIG" --train

  echo "3/7 Features..."
  python -m scripts.features -c "$CONFIG"

  echo "4/7 Labels..."
  python -m scripts.labels -c "$CONFIG"

  echo "4b/7 Check label balance (gate: 20-55% True)..."
  python -m scripts.check_label_balance -c "$CONFIG" || exit 1

  echo "5/7 Train..."
  TRAIN_MODE=$(python -c "
import re, json, sys
with open(sys.argv[1]) as f: s = re.sub(r'//.*', '', f.read())
print(json.loads(s).get('train', False))
" "$CONFIG")
  if [ "$TRAIN_MODE" != "True" ]; then
    echo "ERROR: Config has \"train\": false. Set \"train\": true in $CONFIG so step 5 trains all models (lc, gb, xgb, meta). Then set back to false for the server."
    exit 1
  fi
  python -m scripts.train -c "$CONFIG"

  echo "6/7 Predict..."
  python -m scripts.predict -c "$CONFIG"

  echo "7/7 Signals..."
  python -m scripts.signals -c "$CONFIG"

  SYMBOL=$(python -c "
import re, json, sys
with open(sys.argv[1]) as f: s = re.sub(r'//.*', '', f.read())
print(json.loads(s).get('symbol', ''))
" "$CONFIG")
  echo "Done. signals.csv and predictions.csv are in $DATA_DIR/$SYMBOL/"
  echo "Run backtest: python -m scripts.simulate -c $CONFIG"
done

echo ""
echo "All pipelines finished."
