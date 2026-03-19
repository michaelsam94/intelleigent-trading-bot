# 5min configs: ensemble + LSTM (deep learning branch)

`config-5min-realtime.jsonc` and `config-5min-realtime-ethusdc.jsonc` use a **four-model ensemble** per label:

| Name | Type | Role |
|------|------|------|
| `lc` | Logistic regression | Fast linear baseline |
| `gb` | LightGBM | Gradient boosting on tabular features |
| `xgb` | XGBoost | Second tree ensemble (diversity) |
| `dl` | **Keras LSTM** | Sequence model over the last **48** 5m bars (4h context) |

**Meta-learner:** Ridge regression on **8** inputs (high/low × lc, gb, xgb, dl) produces `trade_score_meta`; the `trade_score` signal column uses the same 8 base predictions.

## Requirements

```bash
pip install tensorflow
```

CPU-only is fine for training/inference at this size. GPU optional.

## After pulling this change

1. **Retrain** (old checkpoints have no `*_dl` models; meta expects 8 columns):
   ```bash
   # Set "train": true, then:
   ./scripts/run_pipeline_to_signals.sh configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc
   ```
2. LSTM models are saved as **`*.keras`** under `data/<SYMBOL>/model/` (not `.pickle`). Tree/linear models stay `.pickle`.

## Roll back to 3 models (no LSTM)

Remove the `dl` algorithm entry, restore meta `base_columns` to 6 names (no `*_dl`), and set combine columns to 3 each — match `config-1min-realtime.jsonc` structure.

## Notes

- LSTM does **not** guarantee better live PnL; crypto 5m is noisy. Always backtest (`scripts/simulate` / `run_backtest_5min.sh`).
- Tuning: `sequence_length` (bars), `units`, `epochs`, `batch_size` in the `dl` algorithm block.
