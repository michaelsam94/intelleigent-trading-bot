#!/usr/bin/env bash
# Run the full pipeline so that predictions.csv and signals.csv exist for backtesting.
# Usage: from project root:
#   ./scripts/run_pipeline_to_signals.sh                    # run both BTCUSDC and ETHUSDC
#   ./scripts/run_pipeline_to_signals.sh <config.jsonc>      # run one config
#   ./scripts/run_pipeline_to_signals.sh <config1> <config2> # run multiple configs
#
# The script sets "train": true before each config's pipeline and restores "train": false on exit
# (success or failure) for every config that was started, so the server does not stay in train mode.

set -e

if [ $# -eq 0 ]; then
  CONFIGS=(configs/config-1min-realtime.jsonc configs/config-1min-realtime-ethusdc.jsonc)
else
  CONFIGS=("$@")
fi

# --- train: true/false toggling (jsonc: first "train" key only) ---
PIPELINE_MANAGED_CONFIGS=()

_set_train_true() {
  python - "$1" <<'PY'
import re, sys
path = sys.argv[1]
with open(path) as f:
    text = f.read()
new = re.sub(r'"train"\s*:\s*false', '"train": true', text, count=1, flags=re.I)
with open(path, "w") as f:
    f.write(new)
PY
}

_set_train_false() {
  python - "$1" <<'PY'
import re, sys
path = sys.argv[1]
with open(path) as f:
    text = f.read()
new = re.sub(r'"train"\s*:\s*true', '"train": false', text, count=1, flags=re.I)
with open(path, "w") as f:
    f.write(new)
PY
}

_restore_train_false_all_managed() {
  for c in "${PIPELINE_MANAGED_CONFIGS[@]}"; do
    [ -f "$c" ] || continue
    echo "  Restoring \"train\": false in $c"
    _set_train_false "$c"
  done
}

trap '_restore_train_false_all_managed' EXIT

for CONFIG in "${CONFIGS[@]}"; do
  echo ""
  echo "=== Pipeline to signals (config: $CONFIG) ==="

  echo "  Setting \"train\": true in $CONFIG (restored to false when this script exits)"
  _set_train_true "$CONFIG"
  PIPELINE_MANAGED_CONFIGS+=("$CONFIG")

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
