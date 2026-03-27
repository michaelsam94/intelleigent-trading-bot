# ETH (multi-coin) technical analysis → Telegram (+ optional TA paper trading)

PM2 app **`eth-ta-telegram`** runs `scripts/eth_ta_telegram.py`: it pulls **Binance spot** OHLCV for several timeframes, computes **TA-Lib** indicators, adds **classic pivot** levels, and sends a **plain-text** digest to Telegram.

## TA paper trading

Set **`TA_TRADE_SIM=1`** in **the project root `.env`** (same folder as `ecosystem.config.cjs`). On startup, **`eth_ta_telegram.py` merges that file into the process environment** (overrides PM2/shell when keys are present), so paper trading can turn on even if PM2 did not pass variables correctly. Still restart after editing: **`pm2 restart eth-ta-telegram --update-env`**.

**Aliases:** **`TA_TRADE_SIM_ENABLED=1`** or **`TA_TRADE_ENABLED=1`** apply if **`TA_TRADE_SIM`** is missing or blank (same pattern as Gemini flags). Use **`TA_TRADE_SIM=1`** to avoid ambiguity.

If logs still show **`trade_sim=False`**, check the startup line **`env check: TA_TRADE_SIM=...`** — if it shows **`None`** or **`'0'`**, the process is not reading your `.env`. Try **`pm2 delete eth-ta-telegram`** then **`pm2 start ecosystem.config.cjs --only eth-ta-telegram`** so PM2 reloads env from disk. Avoid a stray **`TA_TRADE_SIM=`** line with no value unless you rely on **`TA_TRADE_SIM_ENABLED`**.

If PM2 logs show **`trade_sim=False`**, TA-SIM **never** runs — you will get the digest and banners but **no** open/close/TP/SL messages until paper trading is enabled in the env the process sees.

Digest-only mode (no trades) is the default when **`TA_TRADE_SIM`** is unset or **`0`**. Each digest can append a short reminder; set **`TA_SUPPRESS_TRADE_SIM_DIGEST_HINT=1`** to hide it.

- **Starting balance:** `TA_STARTING_BALANCE` (default **$10**)
- **Leverage:** `TA_LEVERAGE` (default **20**)
- **Fees:** `TA_FEE_BPS_PER_SIDE` (default **4** bps/side); P&L matches `outputs/notifier_trades.py` margin-style math
- **State (isolated from ML bot):** `data/ta_sim/<SYMBOL>/` — `position.json`, `balance.json`, `transactions_ta.txt`, `last_close.json`, **`stats.json`** (wins/losses for accuracy)

## Real Binance Futures trading (ETHUSDC)

`eth_ta_telegram.py` now has an env-gated live execution path for USD-M futures:

- `TA_REAL_TRADING=1` and `TA_REAL_CONFIRM=I_UNDERSTAND` are both required.
- Uses **one-way** mode, sets **isolated margin**, applies `TA_LEVERAGE`.
- Entry uses a **LIMIT** order near top-of-book (maker-biased by `TA_REAL_ENTRY_MAKER_OFFSET_BPS`).
- Tries to pre-place TP/SL close-position orders (`TA_REAL_PREPLACE_EXITS=1`), then falls back to placing them immediately after entry fill.
- Uses minimum exchange quantity for initial live test.

Suggested initial env:

```bash
TA_REAL_TRADING=0
TA_REAL_CONFIRM=I_UNDERSTAND
TA_FUTURES_SYMBOL=ETHUSDC
TA_REAL_ENTRY_MAKER_OFFSET_BPS=1.0
TA_REAL_PREPLACE_EXITS=1
TA_REAL_ENTRY_TIMEOUT_SEC=20
TA_REVERSE_SIGNALS=0
```

Switch to live only when ready:

```bash
TA_TRADE_SIM=0
TA_REAL_TRADING=1
```

### Reset balance on restart

Set either:

- `TA_RESET_ON_START=1`, or  
- `TA_RESET_BALANCE_ON_RESTART=1`  

to reset balance to `TA_STARTING_BALANCE`, clear the open position, and **reset `stats.json`** when the process starts (e.g. after `pm2 restart`).

### Entry mode C — one signal per digest (5m TA) + fixed TP/SL (`TA_OPEN_EVERY_DIGEST=1`)

Use this when you want **a new paper trade on every 5m cycle** while **flat** (no overlapping positions):

1. Set **`TA_OPEN_EVERY_DIGEST=1`** (implies **Gemini is not used** for entries).
2. **Direction** from **5m TA score** only: **LONG** if score ≥ 0, else **SHORT**.
3. **TP / SL** — pick one of:
   - **ATR on 5m (recommended for volatility):** set **`TA_TP_SL_USE_ATR=1`**. Uses **ATR(14)** on the 5m bars with **`TA_SIGNAL_TP_ATR_MULT`** / **`TA_SIGNAL_SL_ATR_MULT`** (defaults **2.0** / **1.0** → **2:1** TP:SL distance in **price**). **LONG:** TP = entry + 2×ATR, SL = entry − 1×ATR; **SHORT:** inverted. **Overrides** margin/% for open-every, banner, and **`TA_USE_FIXED_TP_SL_PCT`** paths when ATR is valid.
   - **Fixed %:** if **`TA_TP_SL_USE_ATR=0`**, by default (**`TA_TP_SL_MARGIN_PCT=1`**) **`TA_TP_PRICE_PCT`** / **`TA_SL_PRICE_PCT`** are **margin return** targets (margin return ≈ price move × leverage):
     - Defaults: **`TA_TP_PRICE_PCT=5`**, **`TA_SL_PRICE_PCT=3`** → e.g. **+5% / −3% on margin**; ETH **price** move ≈ **5/L**% / **3/L**% with leverage **L** (`TA_LEVERAGE`, default **20**).
     - Set **`TA_TP_SL_MARGIN_PCT=0`** for legacy behavior: **5% / 3%** on **underlying price** (wide stops on high leverage).
4. **`TA_DIGEST_5M_ONLY=1`** — only compute/send **5m** TA (recommended with this mode).
5. After each **close**, Telegram (or logs) includes **wins, losses, closed count, win rate (accuracy %), balance**.

There is still **no new entry while a position is open**; after TP/SL, **`TA_MIN_BARS_BETWEEN_TRADES`** (default **1** five-minute bar) must pass before the next open.

### Entry quality filters (`TA_SIGNAL_FILTERS`)

Set **`TA_SIGNAL_FILTERS=1`** to require **all enabled** checks below before a TA-SIM **open** (skipped entries are logged to PM2 as `TA-SIM entry skipped: …`):

| Sub-filter | Env | Default | Behavior |
|------------|-----|---------|----------|
| **5m score band** | `TA_SF_SCORE_FILTER` | `1` | **LONG** only if **5m score ≥ `TA_SF_LONG_MIN`** (default **2.0**); **SHORT** only if **≤ `TA_SF_SHORT_MAX`** (default **-2.0**). |
| **Trend on 5m** | `TA_SF_TREND_FILTER` | `1` | **ADX(14) ≥ `TA_SF_ADX_MIN`** (default **20**). Set **`TA_SF_ADX_MIN=-1`** to disable the ADX check only. |
| **MACD vs direction** | `TA_SF_MACD_ALIGN` | `1` | **LONG** requires MACD histogram **> 0**; **SHORT** requires **< 0**. Set **`TA_SF_MACD_ALIGN=0`** to skip. |
| **Higher TF vs counter-trend** | `TA_SF_HTF_FILTER` | `1` | **Skip LONG** if **15m or 1h** TF score **≤ `TA_SF_HT_BEARISH_MAX`** (default **-0.5**). **Skip SHORT** if either score **≥ `TA_SF_HT_BULLISH_MIN`** (default **0.5**). If 15m/1h scores are unavailable, HTF checks are skipped (entry allowed). |

With **`TA_DIGEST_5M_ONLY=1`**, the script **extra-fetches** 15m and 1h klines when **`TA_SIGNAL_FILTERS=1`** so HTF filters work.

**Tighter stops at high leverage:** margin-based SL (**`TA_TP_SL_MARGIN_PCT=1`**) can still be noisy on ETH; widen **`TA_SL_PRICE_PCT`** (margin %) or lower **`TA_LEVERAGE`** (e.g. **10**).

**Fees:** TA-SIM uses **`TA_FEE_BPS_PER_SIDE`** on notional; real exchanges often charge less with maker limits or VIP tiers — this bot does not simulate limit orders; reduce **`TA_FEE_BPS_PER_SIDE`** in `.env` to stress-test “lower fee” outcomes.

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

### Enable / disable Gemini

| Setting | Default | Meaning |
|---------|---------|--------|
| **`TA_USE_GEMINI`** | **`0`** (off) | Set to **`1`** to call Gemini for paper-trade entries when flat (before TA score fallback). |
| **`TA_GEMINI_ENABLED`** | *(unset)* | Same as **`TA_USE_GEMINI`** if **`TA_USE_GEMINI`** is not set (alias). If **both** are set, **`TA_USE_GEMINI`** wins. |
| **`TA_GEMINI_FOR_LIVE`** | **`0`** | Set to **`1`** to allow Gemini direction/TP/SL for **live futures** entries (still one-position-only, falls back to TA rules on failure). |
| **`TA_GEMINI_MASTER_PROMPT`** | **`0`** | Set to **`1`** to use the Master TA prompt format (direction, conviction, entry zone, TP/SL, invalidation fields). |
| **`TA_GEMINI_SIGNAL_EVERY_DIGEST`** | **`0`** | Set to **`1`** to append a Gemini signal block (entry/TP/SL) to every digest message, even when no trade opens. |

Gemini is **not** used when **`TA_OPEN_EVERY_DIGEST=1`** (open-every mode always wins).

PM2 **`ecosystem.config.cjs`** sets **`TA_USE_GEMINI`** from `.env` with default **`0`** so behavior is explicit.

### Entry mode B — Google Gemini (`TA_USE_GEMINI=1`)

1. `pip install google-generativeai` (see `requirements.txt`).
2. Set **`TA_USE_GEMINI=1`** (or **`TA_GEMINI_ENABLED=1`** if you prefer not to set **`TA_USE_GEMINI`**), plus **`GEMINI_API_KEY`** and optionally **`GEMINI_MODEL`** (default **`gemini-1.5-flash`**).
3. The full TA digest + numeric summary is sent to Gemini with a strict JSON-only prompt.
4. Model returns `action` (`LONG` / `SHORT` / `HOLD`), optional `take_profit` / `stop_loss` (absolute prices), `confidence`, `rationale`.
5. If TP/SL pass validation vs entry, those prices are used; otherwise **ATR fallback** keeps Gemini’s direction only.
6. If Gemini returns **`HOLD`**, the API call fails, or the key is missing, the bot **falls through** to the same **TA score** entry rules as mode A (fixed % + fixed TP/SL if enabled, else ATR TP/SL) — so a strong **5m** score can still open a position.
7. **While a position is open**, Gemini is **not** called — only TP/SL checks on 5m bars (same as ML trader). After flat + cooldown, the next cycle may call Gemini again.

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
| `TA_TRADE_SIM` | `0` | **`1`** = run TA paper trades (opens/closes); **`0`** = digest only |
| `TA_TRADE_SIM_ENABLED` | — | Same as **`TA_TRADE_SIM`** when **`TA_TRADE_SIM`** is unset or empty |
| `TA_TRADE_ENABLED` | — | Same as above (alternate name); do not confuse with **`TA_TRADE_SIM`** |
| `TA_SUPPRESS_TRADE_SIM_DIGEST_HINT` | `0` | **`1`** = do not append “TA paper trading is OFF” footer when `TA_TRADE_SIM=0` |
| `TA_STARTUP_TELEGRAM` | `1` | **`1`** = send one **“service started”** message on each process start (e.g. `pm2 restart`); **`0`** = skip |
| `TA_SYMBOL` | `ETHUSDC` | Binance **spot** symbol |
| `TA_INTERVAL_SEC` | `300` | Loop interval (5 min) |
| `TA_KLINES_LIMIT` | `500` | Bars per TF |
| `TA_SIGNAL_ALERTS` | `1` | BULLISH/BEARISH banner (5m label if `TA_SIGNAL_ON_5M=1`, else multi-TF counts) |
| `TA_SIGNAL_ON_5M` | `1` | `1` = 📌 banner + threshold/Gemini entries use **5m** score/label; `0` = mean TF + multi-TF banner |
| `TA_DIGEST_5M_ONLY` | `0` | `1` = only 5m TA in digest |
| `TA_OPEN_EVERY_DIGEST` | `0` | `1` = open when flat each cycle; 5m score sign; fixed TP/SL % |
| `TA_ENTRY_ON_SIGNAL_BANNER` | `0` | `1` = open when 📌 BULLISH/BEARISH banner fires (full digest only); fixed % TP/SL |
| `TA_TP_PRICE_PCT` / `TA_SL_PRICE_PCT` | `5` / `3` | With fixed TP/SL: **margin** % if `TA_TP_SL_MARGIN_PCT=1`, else **underlying** % |
| `TA_TP_SL_USE_ATR` | `0` | `1` = open-every / banner / fixed-% paths use **ATR(14×mult)** on 5m instead of % (see below) |
| `TA_SIGNAL_TP_ATR_MULT` / `TA_SIGNAL_SL_ATR_MULT` | `2` / `1` | TP distance = mult × ATR, SL = mult × ATR (**2:1** default); only if `TA_TP_SL_USE_ATR=1` |
| `TA_TP_SL_MARGIN_PCT` | `1` | `1` = `TA_TP_PRICE_PCT` / `TA_SL_PRICE_PCT` are **margin** targets (price move ÷ leverage); `0` = **spot** % |
| `TA_SIGNAL_FILTERS` | `0` | `1` = stricter TA-SIM entries (score band, ADX/MACD, 15m/1h); see section above |
| `TA_REVERSE_SIGNALS` | `0` | `1` = invert entry direction for TA/Gemini/banner/open-every (LONG signal places SHORT, SHORT signal places LONG) in both TA-SIM and live futures |
| `TA_SF_LONG_MIN` / `TA_SF_SHORT_MAX` | `2.0` / `-2.0` | 5m score limits when `TA_SF_SCORE_FILTER=1` |
| `TA_SF_ADX_MIN` | `20` | Min ADX on 5m; **`-1`** disables ADX check |
| `TA_SF_HT_BEARISH_MAX` / `TA_SF_HT_BULLISH_MIN` | `-0.5` / `0.5` | HTF thresholds for blocking LONG / SHORT |
| `TA_USE_FIXED_TP_SL_PCT` | `0` | `1` = fixed % TP/SL with score thresholds (5m or mean per `TA_SIGNAL_ON_5M`) |
| `TA_USE_GEMINI` | `0` | `1` = Gemini for entries when flat; `0` = TA score only (see **`TA_GEMINI_ENABLED`** alias above) |
| `TA_GEMINI_ENABLED` | — | Alias for **`TA_USE_GEMINI`** when **`TA_USE_GEMINI`** is unset |
| `TA_GEMINI_FOR_LIVE` | `0` | `1` = live futures path can use Gemini action + TP/SL (fallback to TA logic if Gemini fails/returns HOLD) |
| `TA_GEMINI_MASTER_PROMPT` | `0` | `1` = use Master TA prompt JSON schema (direction/conviction/entry zone/invalidation + action/TP/SL) |
| `TA_GEMINI_SIGNAL_EVERY_DIGEST` | `0` | `1` = include Gemini signal section in each digest cycle |
| `GEMINI_API_KEY` | — | Required if Gemini enabled |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Optional model name |

## ML trading vs TA-sim

| | `server-*` (ML) | `eth-ta-telegram` + `TA_TRADE_SIM` |
|--|-----------------|-------------------------------------|
| Signal | `trade_score` + thresholds | **5m score** (default) or mean (`TA_SIGNAL_ON_5M=0`), **Gemini**, or **open every digest** |
| State | `data/<SYMBOL>/` | `data/ta_sim/<SYMBOL>/` |

## Implementation notes

- Indicators are **heuristic** (not identical to TradingView).
- Gemini output is parsed as JSON; **`HOLD` or API errors** fall back to **TA score** entry (mode A), not a hard stop.
