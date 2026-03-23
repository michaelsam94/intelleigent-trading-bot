# ETH (multi-coin) technical analysis → Telegram (+ optional TA paper trading)

PM2 app **`eth-ta-telegram`** runs `scripts/eth_ta_telegram.py`: it pulls **Binance spot** OHLCV for several timeframes, computes **TA-Lib** indicators, adds **classic pivot** levels, and sends a **plain-text** digest to Telegram.

## TA paper trading

Set **`TA_TRADE_SIM=1`** in `.env`.

- **Starting balance:** `TA_STARTING_BALANCE` (default **$10**)
- **Leverage:** `TA_LEVERAGE` (default **20**)
- **Fees:** `TA_FEE_BPS_PER_SIDE` (default **4** bps/side); P&L matches `outputs/notifier_trades.py` margin-style math
- **State (isolated from ML bot):** `data/ta_sim/<SYMBOL>/` — `position.json`, `balance.json`, `transactions_ta.txt`, `last_close.json`

### Reset balance on restart

Set either:

- `TA_RESET_ON_START=1`, or  
- `TA_RESET_BALANCE_ON_RESTART=1`  

to reset balance to `TA_STARTING_BALANCE` and clear the open position when the process starts (e.g. after `pm2 restart`).

### Entry mode A — mean TA score (default when Gemini off)

- **LONG** if mean TF score ≥ `TA_LONG_ENTRY_SCORE` (default **0.8**)
- **SHORT** if mean ≤ `TA_SHORT_ENTRY_SCORE` (default **-0.8**)
- **TP/SL:** ATR(14) on **5m** × `TA_TP_ATR_MULT` / `TA_SL_ATR_MULT` (defaults **4.0** / **2.5**), with **% fallbacks** if ATR missing

### Entry mode B — Google Gemini (`TA_USE_GEMINI=1`)

1. `pip install google-generativeai` (see `requirements.txt`).
2. Set **`GEMINI_API_KEY`** and optionally **`GEMINI_MODEL`** (default **`gemini-1.5-flash`**).
3. The full TA digest + numeric summary is sent to Gemini with a strict JSON-only prompt.
4. Model returns `action` (`LONG` / `SHORT` / `HOLD`), optional `take_profit` / `stop_loss` (absolute prices), `confidence`, `rationale`.
5. If TP/SL pass validation vs entry, those prices are used; otherwise **ATR fallback** keeps Gemini’s direction only.
6. **While a position is open**, Gemini is **not** called — only TP/SL checks on 5m bars (same as ML trader). After flat + cooldown, the next cycle may call Gemini again.

Telegram messages for opens/closes require `TELEGRAM_BOT_TOKEN` + recipients; otherwise PM2 logs only.

### Example `.env`

```bash
TA_TRADE_SIM=1
TA_STARTING_BALANCE=10
TA_LEVERAGE=20
TA_FEE_BPS_PER_SIDE=4
TA_TP_ATR_MULT=4.0
TA_SL_ATR_MULT=2.5
TA_RESET_BALANCE_ON_RESTART=1

# Gemini entries (optional)
TA_USE_GEMINI=1
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-1.5-flash
```

Digest-only (no trades): omit `TA_TRADE_SIM` or set `TA_TRADE_SIM=0`.

## PM2

```bash
pm2 start ecosystem.config.cjs --only eth-ta-telegram
pm2 restart eth-ta-telegram --update-env
pm2 logs eth-ta-telegram
```

## Environment (digest)

| Variable | Default | Meaning |
|----------|---------|---------|
| `TA_SYMBOL` | `ETHUSDC` | Binance **spot** symbol |
| `TA_INTERVAL_SEC` | `300` | Loop interval (5 min) |
| `TA_KLINES_LIMIT` | `500` | Bars per TF |
| `TA_SIGNAL_ALERTS` | `1` | BULLISH/BEARISH banner when many TFs align |

## ML trading vs TA-sim

| | `server-*` (ML) | `eth-ta-telegram` + `TA_TRADE_SIM` |
|--|-----------------|-------------------------------------|
| Signal | `trade_score` + thresholds | Mean TA score **or** Gemini JSON |
| State | `data/<SYMBOL>/` | `data/ta_sim/<SYMBOL>/` |

## Implementation notes

- Indicators are **heuristic** (not identical to TradingView).
- Gemini output is parsed as JSON; failures fall back to no new trade that cycle.
