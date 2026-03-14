# config-1min-realtime.jsonc — Field Reference

Documentation for each field in `configs/config-1min-realtime.jsonc`.

---

## Mode & credentials

| Field | Type | Description |
|-------|------|-------------|
| `train` | boolean | `false` = predict-only (server mode). Use `true` only when running scripts.train to train models. |
| `venue` | string | Data source: `"binance"` |
| `api_key` | string | Binance API key. Overridden by env `BINANCE_API_KEY`. |
| `api_secret` | string | Binance API secret. Overridden by env `BINANCE_API_SECRET`. |
| `telegram_bot_token` | string | Telegram bot token. Overridden by env `TELEGRAM_BOT_TOKEN`. |
| `telegram_chat_id` | string | Telegram chat ID for notifications. Overridden by env `TELEGRAM_CHAT_ID`. |

---

## Data paths & symbol

| Field | Type | Description |
|-------|------|-------------|
| `data_folder` | string | Root folder for data files (e.g. `./data`). |
| `symbol` | string | Trading pair (e.g. `BTCUSDT`). |
| `description` | string | Human-readable description (e.g. `"BTCUSDT 1min realtime, predict next 15 min"`). |

---

## Timeframe & realtime

| Field | Type | Description |
|-------|------|-------------|
| `freq` | string | Pandas frequency: `"1min"` for 1-minute candles. |
| `use_websocket` | boolean | `true` = realtime mode: Binance WebSocket kline stream, no cron. `false` = scheduled mode. |

---

## Horizons & lengths

| Field | Type | Description |
|-------|------|-------------|
| `label_horizon` | number | Number of future bars used for labels. `15` = next 15 minutes. |
| `features_horizon` | number | Minimum lookback (bars) for feature computation. `120` = 2 hours. |
| `train_length` | number | Rows used for training. `40320` ≈ 28 days of 1m bars. `0` = use all available. |
| `predict_length` | number | Rows kept in memory for prediction. `288` ≈ 24 hours. |
| `append_overlap_records` | number | Rows re-fetched and recomputed on each iteration. |

---

## Data sources

| Field | Type | Description |
|-------|------|-------------|
| `download_start_days` | number | When no klines file exists, first download starts this many days before now. Default `60`. Avoids a multi-hour full-history download. |
| `data_sources` | array | Each item: `folder` (symbol folder), `file` (base filename), `column_prefix` (optional). |

---

## Feature generation

| Field | Type | Description |
|-------|------|-------------|
| `feature_sets` | array | Each item defines a feature generator. |
| `feature_sets[].generator` | string | Generator type: `"talib"` for TA-Lib. |
| `feature_sets[].config.columns` | array | Columns used (e.g. `["close"]`). |
| `feature_sets[].config.functions` | array | TA-Lib functions: `SMA`, `LINEARREG_SLOPE`, `STDDEV`. |
| `feature_sets[].config.windows` | array | Window sizes (e.g. `[1, 5, 10, 15, 60]`). |

---

## Label generation

| Field | Type | Description |
|-------|------|-------------|
| `label_sets` | array | Each item defines a label generator. |
| `label_sets[].generator` | string | `"highlow2"` for high/low threshold labels. |
| `label_sets[].config.columns` | array | `["close", "high", "low"]` |
| `label_sets[].config.function` | string | `"high"` = price up, `"low"` = price down. |
| `label_sets[].config.thresholds` | array | Percent move (e.g. `[0.5]` = 0.5% in 15 min). |
| `label_sets[].config.tolerance` | number | Tolerance band (e.g. `0.2`). |
| `label_sets[].config.horizon` | number | Number of future bars used for the label. |
| `label_sets[].config.names` | array | Output column names (e.g. `["high_20"]`). |

---

## Training

| Field | Type | Description |
|-------|------|-------------|
| `train_feature_sets` | array | Feature sets used for training. |
| `train_features` | array | Columns used as model inputs. |
| `labels` | array | Label columns used for training. |
| `algorithms` | array | Each item: `name`, `algo`, `params`, `train`. |
| `algorithms[].name` | string | Name suffix (e.g. `"lc"` → `high_20_lc`). |
| `algorithms[].algo` | string | Algorithm type: `"lc"` = LogisticRegression. |
| `algorithms[].params.is_scale` | boolean | If true, scale inputs before model. |
| `algorithms[].params.allow_constant_fallback` | boolean | If true; when only one class, use constant predictor instead of failing. |
| `algorithms[].train` | object | Sklearn params (e.g. `penalty`, `C`, `solver`, `max_iter`). |

---

## Signals

| Field | Type | Description |
|-------|------|-------------|
| `signal_sets` | array | Each item defines a signal generator. |
| `signal_sets[].generator` | string | `"combine"` or `"threshold_rule"`. |
| `"combine"` config | | `columns`: `["high_20_lc", "low_20_lc"]`, `names`: `"trade_score"`, `combine`: `"difference"`. |
| `"threshold_rule"` config | | `columns`: `"trade_score"`, `parameters.buy_signal_threshold`: `0.015`, `parameters.sell_signal_threshold`: `-0.015`. |

---

## Outputs (Telegram & simulation)

| Field | Type | Description |
|-------|------|-------------|
| `output_sets` | array | Each item defines an output (e.g. Telegram, trader simulation). |
| `score_notification` | boolean | Enable Telegram score notifications. |
| `notify_every_run` | boolean | `true` = every minute; `false` = only when band changes. |
| `score_column_names` | array | Columns to include (e.g. `["trade_score"]`). |
| `notify_band_up` | boolean | Notify when signal strength increases. |
| `notify_band_dn` | boolean | Notify when signal strength decreases. |
| `positive_bands` | array | Each band: `edge`, `sign`, `text`, `bold`, `frequency`. |
| `negative_bands` | array | Same for sell bands. |
| `band.edge` | number | Score threshold (e.g. `0.015` = BUY ZONE). |
| `band.frequency` | number/null | If set, also notify on this time interval (minutes). |

---

## Quick reference

| Purpose | Key fields |
|---------|------------|
| Prediction horizon | `label_horizon`: 15, `label_sets[].config.horizon`: 15 |
| Label sensitivity | `label_sets[].config.thresholds`: `[0.5]` = 0.5% move |
| Buy/sell thresholds | `signal_sets` → `buy_signal_threshold`: 0.015, `sell_signal_threshold`: -0.015 |
| Telegram frequency | `notify_every_run`: false → only on band change |
| Realtime mode | `use_websocket`: true, `freq`: 1min |
| Feature families | Trend (SMA, EMA, LINEARREG_SLOPE), Momentum (RSI, ROC, MOM), Volatility (STDDEV, ATR), Volume (OBV, MFI) |
| Algorithms | `lc` (LogisticRegression) + `gb` (LightGBM). Combine uses `lc` by default; switch to `gb` when both label classes exist. |
