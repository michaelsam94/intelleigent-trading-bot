# ETH (multi-coin) technical analysis → Telegram (+ optional TA paper trading)

PM2 app **`eth-ta-telegram`** runs `scripts/eth_ta_telegram.py`: it pulls **Binance spot** OHLCV for several timeframes, computes **TA-Lib** indicators, adds **classic pivot** levels, and sends a **plain-text** digest to Telegram.

## TA paper trading (same spirit as ML `trader_simulation`)

Set **`TA_TRADE_SIM=1`** in `.env` to **open/close simulated positions** driven by **mean multi-TF TA score** (not the ML `trade_score`):

- **Starting balance:** `TA_STARTING_BALANCE` (default **$10**)
- **Leverage:** `TA_LEVERAGE` (default **20**)
- **TP/SL:** ATR(14) on the **5m** chart × `TA_TP_ATR_MULT` / `TA_SL_ATR_MULT` (defaults **4.0** / **2.5**, same scale as `trader_simulation` in configs); if ATR missing, **% fallbacks** like the ML trader
- **Fees:** `TA_FEE_BPS_PER_SIDE` (default **4** bps/side); margin P&L formula matches `outputs/notifier_trades.py` (open+close on notional, leveraged)
- **State files (isolated from ML bot):** `data/ta_sim/<SYMBOL>/` — `position.json`, `balance.json`, `transactions_ta.txt`, `last_close.json` (does **not** use `data/<SYMBOL>/position.json` used by `service.server`)

**Entry rules (defaults):**

- **LONG** if mean TF score ≥ `TA_LONG_ENTRY_SCORE` (default **0.8**)
- **SHORT** if mean TF score ≤ `TA_SHORT_ENTRY_SCORE` (default **-0.8**)
- Cooldown: `TA_MIN_BARS_BETWEEN_TRADES` **5m bars** after a close (default **1**)

Telegram messages for opens/closes are sent when `TELEGRAM_BOT_TOKEN` + recipients exist; otherwise events are **printed to PM2 logs** only.

### Example `.env`

```bash
TA_TRADE_SIM=1
TA_STARTING_BALANCE=10
TA_LEVERAGE=20
TA_FEE_BPS_PER_SIDE=4
TA_TP_ATR_MULT=4.0
TA_SL_ATR_MULT=2.5
```

Digest-only (no trades): omit `TA_TRADE_SIM` or set `TA_TRADE_SIM=0`.

## PM2

```bash
pm2 start ecosystem.config.cjs --only eth-ta-telegram
pm2 restart eth-ta-telegram --update-env
pm2 logs eth-ta-telegram
```

For digest-only, you need `TELEGRAM_BOT_TOKEN` + recipients. For **trade sim without Telegram**, you can omit token; trades still run and log.

## Environment (digest)

| Variable | Default | Meaning |
|----------|---------|---------|
| `TA_SYMBOL` | `ETHUSDC` | Binance **spot** symbol |
| `TA_INTERVAL_SEC` | `300` | Loop interval (5 min) |
| `TA_KLINES_LIMIT` | `500` | Bars per TF |
| `TA_SIGNAL_ALERTS` | `1` | BULLISH/BEARISH banner when many TFs align |
| `TA_RESET_ON_START` | `0` | `1` = reset TA-sim balance + clear position on process start |

## ML trading vs TA-sim

| | `server-*` (ML) | `eth-ta-telegram` + `TA_TRADE_SIM` |
|--|-----------------|-------------------------------------|
| Signal | `trade_score` + thresholds | Mean multi-TF TA score |
| State | `data/<SYMBOL>/` | `data/ta_sim/<SYMBOL>/` |

They can run side by side without sharing position files.

## Implementation notes

- Indicators are **heuristic** (not identical to TradingView).
- Monthly bars may be short history for MA200.
