# Label balance and retrain checklist

Before any retrain, check label balance. If the numbers are skewed, training will reproduce the same bias (e.g. model only predicts up in a purely bullish window).

## Step 1 — Check label balance (diagnostic)

**Standalone check** (uses matrix file from config — e.g. `data/<SYMBOL>/matrix.csv` or `labels.parquet` if you set `matrix_file_name` to that):

```bash
python -m scripts.check_label_balance -c configs/config-1min-realtime.jsonc
```

**Healthy output** (each label 20–55% True):

```
Label balance (% True). Healthy: 25-55%. Skewed = do not train.
  high_20_03: 38.2% True  [OK]
  high_20_05: 29.1% True  [OK]
  low_20_03:  36.8% True  [OK]
  low_20_05:  28.4% True  [OK]

Balance OK. Proceed with train.
```

**Skewed** (do not train until you fix the window or thresholds):

```
  high_20_05: 68.0% True  [SKEWED]
  low_20_05:  18.0% True  [SKEWED]
At least one label is outside 25-55%. Fix data window or thresholds before training.
```

Exit code: 0 = OK, 1 = skewed, 2 = missing file or columns.

## Step 2 — Pipeline with mandatory balance gate

The pipeline runs the balance check **after labels, before train**. If balance is skewed, the pipeline exits and train is not run.

```bash
# From project root; set "train": true in config
./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime.jsonc
```

Order: Download → Merge (--train) → Features → Labels → **Check label balance** → Train → Predict → Signals.

Only continue to train when all labels are in 20–55%. Do not start the live server until `scripts.simulate` shows positive return over at least 14 days.

## If labels are mostly False (e.g. &lt;15% True)

The gate expects 20–55% True. If you see under 20% True, the label rules are too strict. In the 1min realtime configs we use:

- **thresholds**: `[0.12, 0.2]` — label True when price moves 0.12% or 0.2% in 15 bars.
- **tolerance**: `0.55` — allow pullback before the move so more bars qualify.

Regenerate labels after changing `label_sets` (run pipeline from features → labels, then check balance again). If still skewed, try lower thresholds (e.g. `[0.1, 0.18]`) or higher tolerance (`0.6`).

## Config fixes already applied (1min realtime configs)

- **Class weighting**: LC `class_weight: "balanced"`, solver `saga`, `max_iter: 2000`; GB `is_unbalance: true`, `max_depth: 6`, `learning_rate: 0.02`, `num_boost_round: 500`.
- **Training window**: `train_length: 120000`, `download_start_days: 120` (~83 days of 1m data) so the window can span both bull and bear regimes.
- **Stop loss**: `sl_atr_mult: 2.5` (was 1.5) so SL is not hit by 1m noise; TP/SL ratio 4.0/2.5 is more realistic.

## Manual pipeline with explicit gate

```bash
python -m scripts.download  -c config.json
python -m scripts.merge     -c config.json --train
python -m scripts.features  -c config.json
python -m scripts.labels    -c config.json

# Gate: only continue if all labels are 25–55%
python -m scripts.check_label_balance -c config.json
# If exit code 0:
python -m scripts.train     -c config.json
python -m scripts.predict   -c config.json
python -m scripts.signals   -c config.json
python -m scripts.simulate  -c config.json   # Validate before going live
```
