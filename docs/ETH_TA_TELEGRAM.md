# ETH (multi-coin) technical analysis → Telegram

PM2 app **`eth-ta-telegram`** runs `scripts/eth_ta_telegram.py`: it pulls **Binance spot** OHLCV for several timeframes, computes **TA-Lib** indicators (RSI, MACD, Stochastic, ATR, ADX, CCI, Williams %R, SMA/EMA stack), adds **classic pivot** levels from the previous daily candle, and sends a **plain-text** digest to the same Telegram recipients as the trading bot (`TELEGRAM_BOT_TOKEN` + subscribers / `TELEGRAM_CHAT_ID`).

## PM2

```bash
pm2 start ecosystem.config.cjs --only eth-ta-telegram
pm2 logs eth-ta-telegram
```

Requires `.env` with at least `TELEGRAM_BOT_TOKEN` and one recipient (subscribers via `/start` or `TELEGRAM_CHAT_ID`).

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `TA_SYMBOL` | `ETHUSDC` | Binance **spot** symbol (e.g. `ETHUSDT`) |
| `TA_INTERVAL_SEC` | `300` | Seconds between digests (**5 minutes**) |
| `TA_KLINES_LIMIT` | `500` | Bars per timeframe request |
| `TA_SIGNAL_ALERTS` | `1` | Prepends a **📌 TA SIGNAL** line when many TFs align bullish/bearish |
| `TA_SIGNAL_MIN_TF` | `4` | Min count of Buy/Sell labels across TFs to flag alignment |

Binance keys are **optional** (public klines). If rate-limited, set `BINANCE_API_KEY` / `BINANCE_API_SECRET` in `.env`.

## “Signals” vs ML trading

- **This service** sends **informational** TA summaries and optional **consensus banners** (bullish/bearish alignment). It does **not** place orders or feed `service.server` directly.
- **Your existing servers** (`server-*`) still use **ML scores** + `threshold_rule` for simulated trades. To combine TA with ML you would add a custom gate in code or treat this digest as a manual filter.

## Implementation notes

- Indicators and thresholds are **heuristic** (not identical to TradingView’s UI).
- Monthly (`1M`) bars may have fewer than 200 candles; long MAs are skipped when history is short.
