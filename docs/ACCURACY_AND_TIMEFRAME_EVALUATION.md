# BTC Price Prediction: Accuracy Evaluation & Best Timeframe

## What This Code Actually Predicts

The system **does not predict raw BTC price**. It predicts **binary/classification labels** derived from future price behavior:

- **highlow2 labels** (e.g. `high_20`, `low_20`): “Will the max high (or min low) in the next N bars exceed a given % threshold relative to current close?”
- **topbot labels**: “Is the current bar near a local top or bottom within a tolerance?”

So “accuracy” here means: **how well the model predicts those labels** (e.g. AUC, F1, precision, recall in `common/utils.py`), not point-in-time price or return.

---

## Is the Code Accurate / Methodologically Sound?

### ✅ Strengths (Why It Can Be Accurate)

1. **Correct train/predict split**
   - `label_horizon` excludes the last N rows from training so labels are not built from “future” that leaks into features.
   - Rolling predict (`predict_rolling.py`) does **walk-forward validation**: train on past data, predict the next chunk, repeat. This mimics live use and avoids look-ahead.

2. **Reasonable feature set**
   - TA-Lib: SMA, LINEARREG_SLOPE, STDDEV over multiple windows.
   - Configs use different windows per timeframe (e.g. 1min: 1,5,10,15,60; 1h: 1,3,6,12,24,168,672).
   - StandardScaler before SVC/LC; proper handling of NaNs and infs.

3. **Proper evaluation metrics**
   - Classification: AUC, AP, F1, precision, recall (`compute_scores`).
   - Regression (if used): MAE, MAPE, R2, plus sign-based AUC/F1 (`compute_scores_regression`).
   - Scores are written next to predictions (e.g. `.txt` beside predict output).

4. **Label design**
   - highlow2 uses horizon + threshold + tolerance; topbot uses level + tolerance. Both are well-defined and reproducible.

### ⚠️ Limitations (Why Accuracy Is Bounded)

1. **Market efficiency**
   - Crypto is noisy and partly efficient. Any edge tends to be small and short-lived; backtest performance often overstates live accuracy.

2. **Overfitting risk**
   - 1min config: 525,600 rows, many features → easy to overfit without strong regularization or validation.
   - 1h SVC with fixed C and long history (e.g. 26,280 bars) can memorize structure that doesn’t persist.

3. **Single 0.5 threshold for “accuracy”**
   - Predictions are compared to labels using a fixed 0.5 cutoff for binary metrics. For imbalanced labels (e.g. few “high” events), you may need threshold tuning (your `simulate_model` grid does this for **trade** thresholds, not for **label** accuracy).

4. **No explicit regime handling**
   - No separation of trending vs sideways or high vs low volatility; one model for all regimes can dilute accuracy.

5. **Horizon vs bars**
   - For 1min, `horizon: 120` = 2 hours ahead; for 1h, `horizon: 24` = 1 day ahead. Longer horizon = harder to predict; ensure horizon matches how you intend to trade.

---

## Best Timeframe for “Most Accurate” BTC Predictions

Recommendation: **1h or 4h** for the best trade-off between signal and noise for this codebase.

| Timeframe | Pros | Cons |
|-----------|------|------|
| **1min**  | Many samples, fine granularity | Very noisy; microstructure; more overfitting; harder to generalize |
| **5min / 15min** | More structure than 1m, still many bars | Still noisy; configs not provided (you can copy 1min and change `freq` and windows) |
| **1h** ✅ | Good balance: enough samples (e.g. 8,760/year), clearer trends; configs and horizons (e.g. 24 bars = 1 day) are meaningful | Fewer bars than 1m; need enough history (e.g. 1–3 years) |
| **4h** ✅ | Even cleaner structure; 2,190 bars/year; good for swing-style labels | Fewer samples; need longer history and possibly different horizons |
| **1d** | Very clean, low noise | Very few samples; slow feedback; usually not best for “most accurate” in this setup |

### Practical recommendation

- **Primary:** Use **1h** with:
  - `freq: "1h"`
  - `label_horizon` and label `horizon` in the 12–48 range (e.g. 24 = 1 day).
  - Enough `train_length` (e.g. 1–3 years in bars).
  - Run **rolling predict** and check AUC/F1 in the `.txt` score files; tune thresholds with `simulate_model` for PnL, not just label accuracy.

- **Alternative:** **4h** if you want fewer, higher-confidence signals (e.g. horizon 6–12 bars = 1–2 days). Copy the 1h config, set `freq: "4h"` and adjust `features_horizon`, `train_length`, and label `horizon` to bar counts (not wall-clock).

- **Avoid relying on 1m for “best accuracy”** unless you have strong regularization, out-of-sample/rolling validation, and realistic transaction costs; the code is correct but the timeframe is inherently noisier for this kind of ML.

---

## How to Measure Accuracy in This Repo

1. **Label-level accuracy (classification)**
   - After `predict.py` or `predict_rolling.py`, open the score file next to the prediction output (e.g. `predict_file_name` with `.txt`).
   - Read AUC, F1, precision, recall for each `score_column_name` (e.g. `high_20_lc`, `low_20_svc`).

2. **Trading accuracy**
   - Use `simulate_model` (and backtesting) to optimize buy/sell thresholds.
   - Use `scripts/simulate.py` and backtesting to get PnL and win rate; compare across timeframes (e.g. 1h vs 4h) with the same evaluation period.

3. **Rolling vs one-off**
   - Prefer **rolling predict** for a realistic accuracy estimate (out-of-sample over time). One-off train then predict on the rest can be optimistic.

---

## Short Summary

- **Accuracy:** The **code** is methodologically sound (no look-ahead, proper metrics, walk-forward in rolling predict). Whether predictions are “accurate” depends on **data, horizon, and timeframe**; the repo gives you the tools to measure it (scores + simulation).
- **Best timeframe for more reliable BTC prediction in this setup:** **1h** (or **4h**), with 1h as the default. Use the score `.txt` files and `simulate_model`/backtest to tune and compare.
