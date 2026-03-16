# Action plan phases (score 6.0 → 9.5)

Phased improvements for training rigor, feature quality, regime intelligence, and deep learning.

---

## Phase 1: Training rigor (6.0 → 7.5)

### 1. Walk-forward validation (`predict_rolling`)

The script `scripts/predict_rolling.py` trains on expanding/rolling history and predicts forward, giving a realistic performance estimate.

**Setup:** Ensure the config has a `rolling_predict` block and that `matrix_file_name` is set (default `matrix.csv`). The matrix file is produced by the pipeline after the **labels** step (features + labels).

**Example config block** (add to your config or use a copy of `configs/config-sample-1h.jsonc` which has it):

```jsonc
"matrix_file_name": "matrix.csv",
"rolling_predict": {
  "data_start": null,
  "data_end": null,
  "prediction_start": null,
  "prediction_size": 10080,
  "prediction_steps": 4,
  "use_multiprocessing": false,
  "max_workers": 8
}
```

**Run:**

1. Produce matrix: run the pipeline through the **labels** step (download → merge --train → features → labels). The 1min realtime configs already include `matrix_file_name` and a `rolling_predict` block.
2. Walk-forward:  
   `python -m scripts.predict_rolling -c configs/config-1min-realtime.jsonc`  
   (or your ETHUSDC config).

Adjust `prediction_size` (default 10080 = 1 week of 1m bars) and `prediction_steps` (default 4) in the config if needed.

### 2. Weekly automated retraining (cron)

Retrain models weekly so they don’t go stale.

**Option A – cron using a copy of the config with `"train": true`**

1. Copy your config to e.g. `configs/config-1min-realtime-retrain.jsonc` and set `"train": true`.
2. Crontab (Sunday 00:00; adjust paths):

```cron
0 0 * * 0 cd /path/to/intelligent-trading-bot && ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime-retrain.jsonc 2>&1 | tee -a logs/retrain.log
```

**Option B – weekly retrain script**

`scripts/weekly_retrain.sh` runs download → merge --train → features → labels → train. Use a config with `"train": true` (e.g. duplicate your config to `config-1min-realtime-retrain.jsonc` and set `"train": true` there). Run:

```bash
./scripts/weekly_retrain.sh configs/config-1min-realtime-retrain.jsonc configs/config-1min-realtime-ethusdc-retrain.jsonc
```

Schedule with cron (e.g. Sunday 00:00):  
`0 0 * * 0 cd /path/to/intelligent-trading-bot && ./scripts/weekly_retrain.sh configs/config-1min-realtime-retrain.jsonc configs/config-1min-realtime-ethusdc-retrain.jsonc`  
Then restart the server (e.g. `pm2 restart all`) so it loads the new models.

### 3. XGBoost as third algorithm

**Done.** XGBoost is added as algorithm `xgb` in `common/classifier_xgb.py`, wired in `common/generators.py`, and included in the 1min realtime configs. Install: `pip install xgboost`. Retrain to produce `high_20_05_xgb`, `low_20_05_xgb`, etc. You can add these columns to the signal combine or keep them for a future meta-learner (Phase 4).

---

## Phase 2: Feature quality (7.5 → 8.5)

- **SHAP feature pruning:** Add a script or notebook that runs SHAP on the trained model (e.g. GB/XGB), ranks features, and outputs a pruned `train_features` list (or config overlay).
- **Bollinger Bands:** **Done.** `BBANDS` with window 20 is in `feature_sets`; columns `close_BBANDS_20_0`, `_1`, `_2` are in `train_features`.
- **5-minute timeframe features:** Add a data source or resampled 5m series; compute RSI/trend on 5m and merge as extra columns for the 1m model (e.g. `rsi_5m`, `trend_5m`).
- **Rolling Z-score normalisation:** Add an option or script to normalise features with a rolling Z-score (e.g.  lookback 100 bars) instead of global scaling before training/prediction.

---

## Phase 3: Regime intelligence (8.5 → 9.0)

- **Market regime classifier (HMM 3-state):** Fit a 3-state HMM on returns or volatility; add a `regime` (or regime probabilities) feature; optionally train separate models per regime or gate signals.
- **Adaptive signal thresholds via ATR:** Replace fixed `buy_signal_threshold` / `sell_signal_threshold` with ATR-scaled thresholds (e.g. threshold × (ATR / ATR_baseline)) in the threshold rule or config.
- **Fear & Greed Index:** Fetch a daily Fear & Greed Index (API or CSV), align by date, and add as a daily feature for the 1m model.

---

## Phase 4: Deep learning layer (9.0 → 9.5)

- **LSTM sequence model (PyTorch):** Add a sequence model (e.g. LSTM) that takes windows of features; implement `classifier_lstm.py` with the same train/predict interface; add to `algorithms` and generators.
- **Meta-learner stacking ensemble:** Train a small meta-model (e.g. logistic regression or shallow GB) on `high_20_05_lc`, `high_20_05_gb`, `high_20_05_xgb` (and low) to produce a single `trade_score`; point the signal combine to the meta-model output.
- **Bayesian hyperparameter search (Optuna):** Add an Optuna study over key hyperparameters (e.g. learning_rate, max_depth, num_boost_round) for GB/XGB (and optionally LC), and optionally for the LSTM; save best params to config or a separate config overlay.
