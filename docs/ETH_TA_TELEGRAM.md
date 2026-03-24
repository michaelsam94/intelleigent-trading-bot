# ETH (multi-coin) technical analysis → Telegram (+ optional TA paper trading)

PM2 app **`eth-ta-telegram`** runs `scripts/eth_ta_telegram.py`: it pulls **Binance spot** OHLCV for several timeframes, computes **TA-Lib** indicators, adds **classic pivot** levels, and sends a **plain-text** digest to Telegram.

## TA paper trading

Set **`TA_TRADE_SIM=1`** in `.env`.

- **Starting balance:** `TA_STARTING_BALANCE` (default **$10**)
- **Leverage:** `TA_LEVERAGE` (default **20**)
- **Fees:** `TA_FEE_BPS_PER_SIDE` (default **4** bps/side); P&L matches `outputs/notifier_trades.py` margin-style math
- **State (isolated from ML bot):** `data/ta_sim/<SYMBOL>/` — `position.json`, `balance.json`, `transactions_ta.txt`, `last_close.json`, **`stats.json`** (wins/losses for accuracy)

### Reset balance on restart

Set either:

- `TA_RESET_ON_START=1`, or  
- `TA_RESET_BALANCE_ON_RESTART=1`  

to reset balance to `TA_STARTING_BALANCE`, clear the open position, and **reset `stats.json`** when the process starts (e.g. after `pm2 restart`).

### Entry mode C — one signal per digest (5m TA) + fixed TP/SL (`TA_OPEN_EVERY_DIGEST=1`)

Use this when you want **a new paper trade on every 5m cycle** while **flat** (no overlapping positions):

1. Set **`TA_OPEN_EVERY_DIGEST=1`** (implies **Gemini is not used** for entries).
2. **Direction** from **5m TA score** only: **LONG** if score ≥ 0, else **SHORT**.
3. **TP / SL** as **fixed % moves on the underlying price** (not ATR):
   - Defaults: **`TA_TP_PRICE_PCT=5`**, **`TA_SL_PRICE_PCT=3`**
   - LONG: TP = entry × (1 + 5%), SL = entry × (1 − 3%)
   - SHORT: TP = entry × (1 − 5%), SL = entry × (1 + 3%)
4. **`TA_DIGEST_5M_ONLY=1`** — only compute/send **5m** TA (recommended with this mode).
5. After each **close**, Telegram (or logs) includes **wins, losses, closed count, win rate (accuracy %), balance**.

There is still **no new entry while a position is open**; after TP/SL, **`TA_MIN_BARS_BETWEEN_TRADES`** (default **1** five-minute bar) must pass before the next open.

### 5m vs mean TF for signals (`TA_SIGNAL_ON_5M`, default **on**)

By default (**`TA_SIGNAL_ON_5M=1`**):

- The **📌 TA SIGNAL** banner uses the **5m TF label** only (e.g. **Buy** / **Strong Buy** → BULLISH, **Sell** / **Strong Sell** → BEARISH).
- Paper-trade entries that use score thresholds (**mode A**, fixed-% TP/SL, and the numeric line sent to **Gemini**) use the **5m TA score**, not the mean across timeframes.
- The digest still shows **Summary (mean TF score)** for context, plus a line **Entry signal (5m TF): …** when 5m mode is on.

Set **`TA_SIGNAL_ON_5M=0`** to restore **legacy** behavior: **📌** banner uses **multi-TF label counts** (`TA_SIGNAL_MIN_TF`, default **4** buyish TFs), and entries compare **`TA_LONG_ENTRY_SCORE` / `TA_SHORT_ENTRY_SCORE`** against the **mean TF score** (so a neutral mean can block trades even when several TFs look bullish).

### Banner vs entry (legacy, `TA_SIGNAL_ON_5M=0`)

With mean-based signals off, the **📌** line and **mean score** can disagree — several TFs may be **Buy** while the **mean** stays **Neutral**, so no trade until thresholds are met.

**Ways to align or force entries:**

1. **`TA_ENTRY_ON_SIGNAL_BANNER=1`** — open when **📌 BULLISH** / **📌 BEARISH** fires (fixed **%** TP/SL). With **`TA_SIGNAL_ON_5M=1`** (default), the banner is **5m-based**, so this works with **`TA_DIGEST_5M_ONLY=1`** as well. With **`TA_SIGNAL_ON_5M=0`**, use a **full** digest so the multi-TF banner exists.
2. **`TA_OPEN_EVERY_DIGEST=1`** — open every 5m when flat from **5m score sign** (mode C).
3. **Lower** `TA_LONG_ENTRY_SCORE` / raise `TA_SHORT_ENTRY_SCORE` so weaker scores qualify.

### Entry mode A — score thresholds vs `TA_LONG_ENTRY_SCORE` / `TA_SHORT_ENTRY_SCORE` (when Gemini off)

Uses **`TA_SIGNAL_ON_5M`**: **5m TA score** (default) or **mean TF score** (when `TA_SIGNAL_ON_5M=0`).

- **LONG** if that score ≥ `TA_LONG_ENTRY_SCORE` (default **0.8**)
- **SHORT** if that score ≤ `TA_SHORT_ENTRY_SCORE` (default **-0.8**)
- **TP/SL:** ATR(14) on **5m** × `TA_TP_ATR_MULT` / `TA_SL_ATR_MULT` (defaults **4.0** / **2.5**), with **% fallbacks** if ATR missing

### Entry mode B — Google Gemini (`TA_USE_GEMINI=1`)

1. `pip install google-generativeai` (see `requirements.txt`).
2. Set **`GEMINI_API_KEY`** and optionally **`GEMINI_MODEL`** (default **`gemini-1.5-flash`**).
3. The full TA digest + numeric summary is sent to Gemini with a strict JSON-only prompt.
4. Model returns `action` (`LONG` / `SHORT` / `HOLD`), optional `take_profit` / `stop_loss` (absolute prices), `confidence`, `rationale`.
5. If TP/SL pass validation vs entry, those prices are used; otherwise **ATR fallback** keeps Gemini’s direction only.
6. **While a position is open**, Gemini is **not** called — only TP/SL checks on 5m bars (same as ML trader). After flat + cooldown, the next cycle may call Gemini again.

Telegram messages for opens/closes require `TELEGRAM_BOT_TOKEN` + recipients; otherwise PM2 logs only.

### Example `.env` — one open per 5m digest + 5% / 3% TP/SL

```bash
TA_TRADE_SIM=1
TA_OPEN_EVERY_DIGEST=1
TA_DIGEST_5M_ONLY=1
TA_TP_PRICE_PCT=5
TA_SL_PRICE_PCT=3
TA_STARTING_BALANCE=10
TA_LEVERAGE=20
TA_FEE_BPS_PER_SIDE=4
TA_RESET_BALANCE_ON_RESTART=1
```

### Example `.env` — mean TF score for entries + ATR (not 5m)

```bash
TA_TRADE_SIM=1
TA_SIGNAL_ON_5M=0
TA_STARTING_BALANCE=10
TA_LEVERAGE=20
TA_FEE_BPS_PER_SIDE=4
TA_TP_ATR_MULT=4.0
TA_SL_ATR_MULT=2.5
TA_RESET_BALANCE_ON_RESTART=1

# Gemini entries (optional; not used with TA_OPEN_EVERY_DIGEST=1)
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
| `TA_SIGNAL_ALERTS` | `1` | BULLISH/BEARISH banner (5m label if `TA_SIGNAL_ON_5M=1`, else multi-TF counts) |
| `TA_SIGNAL_ON_5M` | `1` | `1` = 📌 banner + threshold/Gemini entries use **5m** score/label; `0` = mean TF + multi-TF banner |
| `TA_DIGEST_5M_ONLY` | `0` | `1` = only 5m TA in digest |
| `TA_OPEN_EVERY_DIGEST` | `0` | `1` = open when flat each cycle; 5m score sign; fixed TP/SL % |
| `TA_ENTRY_ON_SIGNAL_BANNER` | `0` | `1` = open when 📌 BULLISH/BEARISH banner fires (full digest only); fixed % TP/SL |
| `TA_TP_PRICE_PCT` / `TA_SL_PRICE_PCT` | `5` / `3` | Fixed TP/SL % on price (with open-every or `TA_USE_FIXED_TP_SL_PCT`) |
| `TA_USE_FIXED_TP_SL_PCT` | `0` | `1` = fixed % TP/SL with score thresholds (5m or mean per `TA_SIGNAL_ON_5M`) |

## ML trading vs TA-sim

| | `server-*` (ML) | `eth-ta-telegram` + `TA_TRADE_SIM` |
|--|-----------------|-------------------------------------|
| Signal | `trade_score` + thresholds | **5m score** (default) or mean (`TA_SIGNAL_ON_5M=0`), **Gemini**, or **open every digest** |
| State | `data/<SYMBOL>/` | `data/ta_sim/<SYMBOL>/` |

## Implementation notes

- Indicators are **heuristic** (not identical to TradingView).
- Gemini output is parsed as JSON; failures fall back to no new trade that cycle.
