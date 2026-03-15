#!/usr/bin/env bash
# Run the full pipeline so that predictions.csv and signals.csv exist for backtesting.
# Usage: from project root:
#   ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime.jsonc
#
# For training you need "train": true in the config. Set it back to false for the server.

set -e
CONFIG="${1:?Usage: $0 <config.jsonc>}"

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

echo "2/7 Merge (--train for 60k bars)..."
python -m scripts.merge -c "$CONFIG" --train

echo "3/7 Features..."
python -m scripts.features -c "$CONFIG"

echo "4/7 Labels..."
python -m scripts.labels -c "$CONFIG"

echo "5/7 Train (ensure \"train\": true in config for this step)..."
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
