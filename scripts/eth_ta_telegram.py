#!/usr/bin/env python3
"""
Multi-timeframe technical analysis digest for ETH (or any Binance spot symbol), sent to Telegram every N seconds.

Optional TA-driven **paper trading** (isolated state under data/ta_sim/<SYMBOL>/): $10 start, 20x leverage,
ATR TP/SL and same fee model as outputs/notifier_trades trader_simulation.

Env (digest):
  TA_SYMBOL=ETHUSDC
  TA_INTERVAL_SEC=300
  TA_KLINES_LIMIT=500
  TA_SIGNAL_ALERTS=1
  TELEGRAM_BOT_TOKEN=...

Env (TA trade sim — set TA_TRADE_SIM=1 or no TA-SIM opens/closes are sent):
  TA_TRADE_SIM=1              # or TA_TRADE_SIM_ENABLED / TA_TRADE_ENABLED; script merges project .env on startup
  TA_STARTING_BALANCE=10
  TA_LEVERAGE=20
  TA_FEE_BPS_PER_SIDE=4
  TA_TP_ATR_MULT=4.0
  TA_SL_ATR_MULT=2.5
  TA_TP_PCT_FALLBACK=0.15
  TA_SL_PCT_FALLBACK=0.1
  TA_LONG_ENTRY_SCORE=0.8      # mean TF score >= this → open LONG
  TA_SHORT_ENTRY_SCORE=-0.8    # mean TF score <= this → open SHORT
  TA_MIN_BARS_BETWEEN_TRADES=2 # 5m bars after a close before new entry (reduces fee drag)
  TA_STATE_DIR=data/ta_sim     # isolated from ML trader position.json
  TA_RESET_BALANCE_ON_RESTART=1  # reset balance, position, stats, last_close cooldown on process start

  TA_OPEN_EVERY_DIGEST=1       # one new trade each digest when flat; direction from 5m TA score (>=0 LONG else SHORT)
  TA_DIGEST_5M_ONLY=1          # only 5m TA in Telegram/API (lighter)
  TA_TP_PRICE_PCT=6            # fixed TP % margin (wider TP vs SL helps net edge at high leverage; tune via optimize_ta_backtest)
  TA_SL_PRICE_PCT=2.5          # fixed SL % margin
  TA_TP_SL_MARGIN_PCT=1        # 1=TP/SL % are margin P&L (÷ leverage → price); 0=underlying price %
  TA_TP_SL_USE_ATR=0           # 1=fixed TP/SL paths use ATR(14) on 5m (see TA_SIGNAL_*_ATR_MULT); overrides margin/%
  TA_SIGNAL_TP_ATR_MULT=2.0    # TP distance = mult × ATR (default 2 for 2:1 vs SL)
  TA_SIGNAL_SL_ATR_MULT=1.0    # SL distance = mult × ATR (default 1)

  TA_USE_GEMINI=0              # 1=enable Gemini for entries; 0=disable (TA score only). Alias: TA_GEMINI_ENABLED
  TA_GEMINI_ENABLED=0          # if TA_USE_GEMINI unset, same meaning as TA_USE_GEMINI
  GEMINI_API_KEY=...           # required when Gemini enabled
  GEMINI_MODEL=gemini-1.5-flash

  TA_ENTRY_ON_SIGNAL_BANNER=0  # if 1: open LONG/SHORT when 📌 BULLISH/BEARISH banner fires (same TP%/SL% as open-every); falls back to Gemini/mean if no banner
  TA_SIGNAL_ON_5M=1           # if 1 (default): 📌 banner + mean-score/Gemini entries use 5m TF score/label, not mean TF score; set 0 for legacy mean-TF behavior

  TA_SIGNAL_FILTERS=1         # 0=looser entries; 1=stricter: score band, ADX+MACD, 15m/1h (see docs). Tune via scripts/optimize_ta_backtest.py
  TA_SF_SCORE_FILTER=1        # 5m score band (with TA_SF_LONG_MIN / TA_SF_SHORT_MAX)
  TA_SF_LONG_MIN=2.0          # LONG only if 5m score >= this
  TA_SF_SHORT_MAX=-2.0        # SHORT only if 5m score <= this
  TA_SF_TREND_FILTER=1        # ADX + MACD alignment on 5m
  TA_SF_ADX_MIN=20            # set -1 to skip ADX check only
  TA_SF_MACD_ALIGN=1          # LONG: MACD hist > 0; SHORT: < 0
  TA_SF_MACD_BYPASS_STRONG_5M=0  # if 1: skip MACD gate when 5m label matches side (Strong Buy→LONG, Strong Sell→SHORT)
  TA_SF_HTF_FILTER=1          # skip LONG if 15m/1h bearish; skip SHORT if bullish
  TA_SF_HT_BEARISH_MAX=-0.5   # HTF score at/below = bearish (blocks LONG)
  TA_SF_HT_BULLISH_MIN=0.5    # HTF score at/above = bullish (blocks SHORT)

  TA_STARTUP_TELEGRAM=1       # 1=send one Telegram message on process start (restart)

  Preset (optional — matches scripts/backtest_ta_signals.py --preset high-win-rate):
  TA_PRESET=high-win-rate     # or TA_HIGH_WIN_RATE=1; disable with TA_PRESET=none
    → TA_LEVERAGE=3 TA_MIN_BARS_BETWEEN_TRADES=24 TA_TP_PRICE_PCT=1.2 TA_SL_PRICE_PCT=10
    → TA_TP_SL_USE_ATR=0 TA_SF_LONG_MIN=2.5 TA_SF_SHORT_MAX=-2.5 TA_OPEN_EVERY_MIN_ABS_SCORE=2.2
    → TA_SIGNAL_FILTERS=1 TA_ENTRY_ON_SIGNAL_BANNER=0
  TA_OPEN_EVERY_MIN_ABS_SCORE=0   # when TA_OPEN_EVERY_DIGEST=1: only LONG if 5m score >= N, SHORT if <= -N; 0 = sign-only
  TA_OPEN_EVERY_STRONG_5M_ONLY=0  # when 1: open-every only if 5m label is Strong Buy (LONG) or Strong Sell (SHORT); ignores min-abs gate
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_project_dotenv() -> None:
    """Merge project root .env into os.environ (overwrites). Same line rules as ecosystem.config.cjs."""
    p = _ROOT / ".env"
    if not p.is_file():
        return
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        eq = line.index("=")
        key = line[:eq].strip()
        val = line[eq + 1 :].strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("\\'", "'")
        if key:
            os.environ[key] = val


def _env_preset_name() -> str:
    """TA_PRESET=high-win-rate or TA_HIGH_WIN_RATE=1 → high-win-rate bundle (see _apply_ta_preset)."""
    p = (os.environ.get("TA_PRESET") or "").strip().lower()
    if p in ("", "none", "off", "0", "false"):
        p = ""
    if p:
        return p
    if os.environ.get("TA_HIGH_WIN_RATE", "").strip().lower() in ("1", "true", "yes", "on"):
        return "high-win-rate"
    return ""


def _apply_ta_preset() -> None:
    """
    Align live TA-SIM with scripts/backtest_ta_signals.py --preset high-win-rate.

    Set TA_PRESET=high-win-rate (or TA_HIGH_WIN_RATE=1). Disable with TA_PRESET=none.
    Uses setdefault: keys already set (e.g. from .env after _load_project_dotenv) are not overwritten.
    """
    name = _env_preset_name()
    if name != "high-win-rate":
        return
    # Mirrors backtest_ta_signals.py preset high-win-rate (tight TP / wide SL on margin, selective entries).
    bundle = {
        "TA_LEVERAGE": "3",
        "TA_MIN_BARS_BETWEEN_TRADES": "24",
        "TA_TP_PRICE_PCT": "1.2",
        "TA_SL_PRICE_PCT": "10.0",
        "TA_TP_SL_USE_ATR": "0",
        "TA_SF_LONG_MIN": "2.5",
        "TA_SF_SHORT_MAX": "-2.5",
        "TA_OPEN_EVERY_MIN_ABS_SCORE": "2.2",
        "TA_SIGNAL_FILTERS": "1",
        "TA_ENTRY_ON_SIGNAL_BANNER": "0",
    }
    for k, v in bundle.items():
        os.environ.setdefault(k, v)


import talib

from binance import Client
from binance.exceptions import BinanceAPIException

from common.telegram_broadcast import broadcast_telegram_plain, recipient_chat_ids
from common.gemini_ta import run_gemini_decision, validate_tp_sl

_KLINE_COLS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_av",
    "trades",
    "tb_base_av",
    "tb_quote_av",
    "ignore",
]


def _klines_to_df(klines: list) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=_KLINE_COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _client() -> Client:
    k = os.environ.get("BINANCE_API_KEY", "").strip()
    s = os.environ.get("BINANCE_API_SECRET", "").strip()
    if k and s:
        return Client(k, s)
    return Client("", "")


def _last(x: np.ndarray | None) -> float | None:
    if x is None or len(x) < 1:
        return None
    v = float(x[-1])
    if np.isnan(v):
        return None
    return v


def _signal_on_5m() -> bool:
    """When True, signal banner and entry thresholds use 5m TF score/label (default on)."""
    return os.environ.get("TA_SIGNAL_ON_5M", "1").strip().lower() in ("1", "true", "yes", "on")


def _ta_trade_sim_enabled() -> bool:
    """
    Paper trading when TA_TRADE_SIM is truthy, else TA_TRADE_SIM_ENABLED or TA_TRADE_ENABLED
    (common typo) if TA_TRADE_SIM is unset/empty.
    Strips UTF-8 BOM from values (bad .env / exports).
    """

    def _norm(s: str) -> str:
        t = str(s).strip()
        if t.startswith("\ufeff"):
            t = t.lstrip("\ufeff").strip()
        return t.lower()

    primary = os.environ.get("TA_TRADE_SIM")
    if primary is not None and _norm(primary) != "":
        v = _norm(primary)
    else:
        alt = (
            os.environ.get("TA_TRADE_SIM_ENABLED")
            or os.environ.get("TA_TRADE_ENABLED")
            or "0"
        )
        v = _norm(alt)
    return v in ("1", "true", "yes", "on")


def _ta_real_trading_enabled() -> bool:
    """Real Binance Futures trading gate: requires both TA_REAL_TRADING=1 and TA_REAL_CONFIRM=I_UNDERSTAND."""
    if os.environ.get("TA_REAL_TRADING", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    return os.environ.get("TA_REAL_CONFIRM", "").strip().upper() == "I_UNDERSTAND"


def _suppress_trade_sim_digest_hint() -> bool:
    return os.environ.get("TA_SUPPRESS_TRADE_SIM_DIGEST_HINT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _gemini_entries_env_enabled() -> bool:
    """
    Whether Gemini is enabled for paper-trade entries (before TA_OPEN_EVERY_DIGEST turns it off).
    TA_USE_GEMINI wins if set; otherwise TA_GEMINI_ENABLED (default off).
    """
    primary = os.environ.get("TA_USE_GEMINI")
    if primary is not None and str(primary).strip() != "":
        v = str(primary).strip().lower()
    else:
        v = (os.environ.get("TA_GEMINI_ENABLED") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _gemini_live_entries_enabled() -> bool:
    """Whether live futures path may use Gemini direction/TP/SL (opt-in)."""
    return _gemini_entries_env_enabled() and _sf_sub("TA_GEMINI_FOR_LIVE", "0")


def _gemini_signal_digest_enabled() -> bool:
    """Whether to append a Gemini signal section to every 5m digest."""
    return _gemini_entries_env_enabled() and _sf_sub("TA_GEMINI_SIGNAL_EVERY_DIGEST", "0")


def _signal_filters_enabled() -> bool:
    """Stricter TA-SIM entry gates (score band, ADX/MACD, higher-TF trend)."""
    return os.environ.get("TA_SIGNAL_FILTERS", "1").strip().lower() in ("1", "true", "yes", "on")


def _sf_sub(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _reverse_signals_enabled() -> bool:
    """If enabled, invert entry direction (LONG <-> SHORT) for TA/Gemini/banner/open-every entries."""
    return os.environ.get("TA_REVERSE_SIGNALS", "0").strip().lower() in ("1", "true", "yes", "on")


def _opposite_side(side: str) -> str:
    s = (side or "").strip().upper()
    if s == "LONG":
        return "SHORT"
    if s == "SHORT":
        return "LONG"
    return s


def _reverse_side_and_levels(side: str, entry_price: float, tp_price: float, sl_price: float) -> tuple[str, float, float]:
    """
    Flip LONG<->SHORT and mirror TP/SL around entry using original distances.
    This preserves configured risk/reward distances while reversing direction.
    """
    s = (side or "").strip().upper()
    if s not in ("LONG", "SHORT"):
        return side, tp_price, sl_price
    tp_dist = abs(float(tp_price) - float(entry_price))
    sl_dist = abs(float(sl_price) - float(entry_price))
    rs = _opposite_side(s)
    if rs == "LONG":
        return rs, float(entry_price) + tp_dist, float(entry_price) - sl_dist
    return rs, float(entry_price) - tp_dist, float(entry_price) + sl_dist


def _tf_label(score: float) -> str:
    if score >= 2.5:
        return "Strong Buy"
    if score >= 0.8:
        return "Buy"
    if score <= -2.5:
        return "Strong Sell"
    if score <= -0.8:
        return "Sell"
    return "Neutral"


def _ma_score(close: float, mas: dict[str, float | None]) -> tuple[float, int, int]:
    b, s = 0, 0
    for _name, ma in mas.items():
        if ma is None or ma <= 0:
            continue
        if close > ma:
            b += 1
        elif close < ma:
            s += 1
    net = (b - s) * 0.25
    return net, b, s


def _analyze_ohlcv(df: pd.DataFrame) -> tuple[float, dict[str, str]]:
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(close)
    details: dict[str, str] = {}
    if n < 60:
        return 0.0, {"error": "not enough bars"}

    score = 0.0

    periods = [5, 10, 20, 50, 100, 200]
    mas: dict[str, float | None] = {}
    for p in periods:
        if n < p:
            continue
        sma = _last(talib.SMA(close, timeperiod=p))
        ema = _last(talib.EMA(close, timeperiod=p))
        mas[f"SMA{p}"] = sma
        mas[f"EMA{p}"] = ema
    c = float(close[-1])
    net_ma, buy_m, sell_m = _ma_score(c, mas)
    score += net_ma
    details["MA"] = f"Neutral ({buy_m} buy, {sell_m} sell) vs SMA/EMA"

    rsi = _last(talib.RSI(close, timeperiod=14))
    if rsi is not None:
        if rsi < 30:
            score += 1.0
            details["RSI(14)"] = f"{rsi:.2f} Oversold"
        elif rsi > 70:
            score -= 1.0
            details["RSI(14)"] = f"{rsi:.2f} Overbought"
        else:
            details["RSI(14)"] = f"{rsi:.2f} Neutral"

    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    h = _last(macd_hist)
    if h is not None:
        if h > 0:
            score += 0.5
            details["MACD"] = "Buy"
        elif h < 0:
            score -= 0.5
            details["MACD"] = "Sell"
        else:
            details["MACD"] = "Neutral"

    slowk, slowd = talib.STOCH(high, low, close, fastk_period=5, slowk_period=3, slowd_period=3)
    sk = _last(slowk)
    if sk is not None:
        if sk < 20:
            score += 0.4
            details["STOCH"] = f"{sk:.1f} Oversold"
        elif sk > 80:
            score -= 0.4
            details["STOCH"] = f"{sk:.1f} Overbought"
        else:
            details["STOCH"] = f"{sk:.1f} Neutral"

    atr = _last(talib.ATR(high, low, close, timeperiod=14))
    if atr is not None:
        rel = atr / c * 100.0 if c else 0.0
        details["ATR(14)"] = f"{atr:.4f} ({rel:.2f}% of price) Volatility"

    adx = _last(talib.ADX(high, low, close, timeperiod=14))
    if adx is not None:
        if adx > 25:
            plus_di = _last(talib.PLUS_DI(high, low, close, timeperiod=14))
            minus_di = _last(talib.MINUS_DI(high, low, close, timeperiod=14))
            if plus_di is not None and minus_di is not None:
                if plus_di > minus_di:
                    score += 0.4
                    details["ADX(14)"] = f"{adx:.1f} Buy (trend up)"
                else:
                    score -= 0.4
                    details["ADX(14)"] = f"{adx:.1f} Sell (trend dn)"
            else:
                details["ADX(14)"] = f"{adx:.1f}"
        else:
            details["ADX(14)"] = f"{adx:.1f} Weak trend"

    cci = _last(talib.CCI(high, low, close, timeperiod=14))
    if cci is not None:
        if cci > 100:
            score -= 0.3
            details["CCI(14)"] = f"{cci:.1f} Sell"
        elif cci < -100:
            score += 0.3
            details["CCI(14)"] = f"{cci:.1f} Buy"
        else:
            details["CCI(14)"] = f"{cci:.1f} Neutral"

    willr = _last(talib.WILLR(high, low, close, timeperiod=14))
    if willr is not None:
        if willr < -80:
            score += 0.3
            details["WilliamsR"] = f"{willr:.1f} Oversold"
        elif willr > -20:
            score -= 0.3
            details["WilliamsR"] = f"{willr:.1f} Overbought"
        else:
            details["WilliamsR"] = f"{willr:.1f} Neutral"

    return score, details


def _adx_macd_from_df(df: pd.DataFrame) -> tuple[float | None, float | None]:
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    if len(close) < 60:
        return None, None
    adx = _last(talib.ADX(high, low, close, timeperiod=14))
    _m, _s, hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    return adx, _last(hist)


def _pivot_classic(prev: pd.Series) -> dict[str, float]:
    h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
    pp = (h + l + c) / 3.0
    r1 = 2 * pp - l
    s1 = 2 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)
    r3 = h + 2 * (pp - l)
    s3 = l - 2 * (h - pp)
    return {"P": pp, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}


@dataclass
class TASnapshot:
    text: str
    banner: str | None
    tf_scores: list[float]
    tf_labels: list[str]
    mean_score: float
    score_5m: float  # first TF in list (5m) — used for direction when TA_OPEN_EVERY_DIGEST=1
    score_for_entry: float  # 5m or mean per TA_SIGNAL_ON_5M — thresholds TA_LONG_ENTRY_SCORE / TA_SHORT_ENTRY_SCORE
    entry_score_kind: str  # "5m" or "mean"
    label_5m: str  # _tf_label(score_5m) string e.g. Buy, Neutral
    df_5m: pd.DataFrame | None
    htf_scores: dict[str, float] = field(default_factory=dict)  # 15m / 1h TF scores for entry filters


def build_snapshot(symbol: str, limit: int) -> TASnapshot:
    client = _client()
    digest_5m_only = os.environ.get("TA_DIGEST_5M_ONLY", "0").strip().lower() in ("1", "true", "yes", "on")
    frames = (
        [("5m", "5 Min")]
        if digest_5m_only
        else [
            ("5m", "5 Min"),
            ("15m", "15 Min"),
            ("30m", "30 Min"),
            ("1h", "Hourly"),
            ("1d", "Daily"),
            ("1w", "Weekly"),
            ("1M", "Monthly"),
        ]
    )
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"📊 TA digest — {symbol} (Binance spot)")
    lines.append(f"As of {now}")
    lines.append("")

    tf_scores: list[float] = []
    tf_labels: list[str] = []
    df_5m: pd.DataFrame | None = None

    for interval, label in frames:
        try:
            kl = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        except BinanceAPIException as e:
            lines.append(f"{label}: (error: {e})")
            continue
        if not kl:
            lines.append(f"{label}: no data")
            continue
        df = _klines_to_df(kl)
        if interval == "5m":
            df_5m = df
        sc, det = _analyze_ohlcv(df)
        tf_scores.append(sc)
        lab = _tf_label(sc)
        tf_labels.append(lab)
        lines.append(f"── {label} ──  {_tf_label(sc)}")
        price = float(df["close"].iloc[-1])
        lines.append(f"  Close: {price:,.2f}")
        for k in sorted(det.keys()):
            lines.append(f"  {k}: {det[k]}")
        lines.append("")

    if not digest_5m_only:
        try:
            kl_d = client.get_klines(symbol=symbol, interval="1d", limit=5)
            if len(kl_d) >= 2:
                ddf = _klines_to_df(kl_d)
                prev = ddf.iloc[-2]
                pv = _pivot_classic(prev)
                lines.append("── Pivot (Classic, prev daily) ──")
                for k in ("R3", "R2", "R1", "P", "S1", "S2", "S3"):
                    lines.append(f"  {k}: {pv[k]:,.2f}")
                lines.append("")
        except Exception as e:
            lines.append(f"Pivot: (skip {e})")
            lines.append("")

    htf_scores: dict[str, float] = {}
    if not digest_5m_only and len(tf_scores) >= 4:
        htf_scores["15m"] = float(tf_scores[1])
        htf_scores["1h"] = float(tf_scores[3])
    elif digest_5m_only and _signal_filters_enabled():
        for interval, key in (("15m", "15m"), ("1h", "1h")):
            try:
                kl = client.get_klines(symbol=symbol, interval=interval, limit=limit)
                if kl and len(kl) >= 60:
                    dfx = _klines_to_df(kl)
                    sc, _ = _analyze_ohlcv(dfx)
                    htf_scores[key] = float(sc)
            except Exception:
                pass

    mean_score = float(np.mean(tf_scores)) if tf_scores else 0.0
    score_5m = float(tf_scores[0]) if tf_scores else 0.0
    label_5m = tf_labels[0] if tf_labels else "N/A"
    use_5m_signal = _signal_on_5m()
    score_for_entry = score_5m if use_5m_signal else mean_score
    entry_score_kind = "5m" if use_5m_signal else "mean"
    if tf_scores:
        overall = _tf_label(mean_score)
        lines.append(f"Summary (mean TF score): {overall}")
        lines.append(f"5m score: {score_5m:+.4f} | TF labels: {', '.join(tf_labels)}")
        if use_5m_signal:
            lines.append(f"Entry signal (5m TF): {label_5m} (score {score_5m:+.4f})")
        if _signal_filters_enabled():
            lines.append(
                "TA_SIGNAL_FILTERS: ON — paper-trade entries require score band, ADX+MACD, 15m/1h trend (see docs)"
            )
            if htf_scores:
                lines.append(
                    f"HTF for filters: 15m {htf_scores.get('15m', float('nan')):+.4f} | "
                    f"1h {htf_scores.get('1h', float('nan')):+.4f}"
                )
    else:
        overall = "N/A"

    signal_banner: str | None = None
    if os.environ.get("TA_SIGNAL_ALERTS", "1").strip().lower() in ("1", "true", "yes", "on"):
        if use_5m_signal and tf_labels:
            lab5 = tf_labels[0]
            if lab5 in ("Strong Buy", "Buy"):
                signal_banner = "📌 TA SIGNAL: BULLISH (5m TF)"
            elif lab5 in ("Strong Sell", "Sell"):
                signal_banner = "📌 TA SIGNAL: BEARISH (5m TF)"
        elif not digest_5m_only:
            strong_buy = sum(1 for x in tf_labels if x == "Strong Buy")
            strong_sell = sum(1 for x in tf_labels if x == "Strong Sell")
            buyish = sum(1 for x in tf_labels if x in ("Strong Buy", "Buy"))
            sellish = sum(1 for x in tf_labels if x in ("Strong Sell", "Sell"))
            thr = int(os.environ.get("TA_SIGNAL_MIN_TF", "4"))
            if strong_buy >= 2 or buyish >= thr:
                signal_banner = "📌 TA SIGNAL: BULLISH (multi-TF alignment)"
            elif strong_sell >= 2 or sellish >= thr:
                signal_banner = "📌 TA SIGNAL: BEARISH (multi-TF alignment)"

    return TASnapshot(
        text="\n".join(lines),
        banner=signal_banner,
        tf_scores=tf_scores,
        tf_labels=tf_labels,
        mean_score=mean_score,
        score_5m=score_5m,
        score_for_entry=score_for_entry,
        entry_score_kind=entry_score_kind,
        label_5m=label_5m,
        df_5m=df_5m,
        htf_scores=htf_scores,
    )


# --- TA paper trading (isolated paths) ---


def _ta_dir(symbol: str) -> Path:
    base = os.environ.get("TA_STATE_DIR", "data/ta_sim").strip()
    return _ROOT / base / symbol


def _pos_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "position.json"


def _bal_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "balance.json"


def _stats_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "stats.json"


def _load_stats(symbol: str) -> dict[str, int]:
    p = _stats_path(symbol)
    if not p.is_file():
        return {"wins": 0, "losses": 0}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return {"wins": int(d.get("wins", 0)), "losses": int(d.get("losses", 0))}
    except Exception:
        return {"wins": 0, "losses": 0}


def _save_stats(symbol: str, wins: int, losses: int) -> None:
    p = _stats_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"wins": wins, "losses": losses}, f, indent=2)


def _tx_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "transactions_ta.txt"


def _last_close_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "last_close.json"


def _load_position(symbol: str) -> dict | None:
    p = _pos_path(symbol)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("open") else None
    except Exception:
        return None


def _save_position(symbol: str, data: dict) -> None:
    p = _pos_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=0)


def _clear_position(symbol: str) -> None:
    p = _pos_path(symbol)
    if p.is_file():
        p.unlink()


def _clear_last_close(symbol: str) -> None:
    """Remove post-close cooldown marker (used when resetting TA-SIM state on start)."""
    p = _last_close_path(symbol)
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass


def _load_balance(symbol: str) -> tuple[float, float]:
    start = float(os.environ.get("TA_STARTING_BALANCE", "10"))
    p = _bal_path(symbol)
    if not p.is_file():
        return start, start
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return float(data.get("balance", start)), float(data.get("starting_balance", start))
    except Exception:
        return start, start


def _save_balance(symbol: str, balance: float, starting: float) -> None:
    p = _bal_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"balance": balance, "starting_balance": starting}, f)


def _save_last_close(symbol: str, close_time) -> None:
    p = _last_close_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"last_close_time": str(close_time)}, f)


def _load_last_close(symbol: str) -> str | None:
    p = _last_close_path(symbol)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("last_close_time")
    except Exception:
        return None


def _atr_from_df(df: pd.DataFrame) -> float | None:
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    if len(close) < 20:
        return None
    a = _last(talib.ATR(high, low, close, timeperiod=14))
    return a


def _bar_close_time_5m(df: pd.DataFrame):
    ts = df["timestamp"].iloc[-1]
    return pd.Timestamp(ts) + pd.Timedelta(minutes=5)


def _banner_entry_side(banner: str | None) -> str | None:
    """LONG/SHORT from multi-TF signal banner text, or None."""
    if not banner:
        return None
    u = banner.upper()
    if "BULLISH" in u:
        return "LONG"
    if "BEARISH" in u:
        return "SHORT"
    return None


def _tp_sl_fixed_price_pct(side: str, entry: float, tp_pct: float, sl_pct: float) -> tuple[float, float]:
    """TP/SL as percent move on underlying price (e.g. +5% TP, -3% SL for LONG)."""
    tpp = tp_pct / 100.0
    slp = sl_pct / 100.0
    if side == "LONG":
        return entry * (1.0 + tpp), entry * (1.0 - slp)
    return entry * (1.0 - tpp), entry * (1.0 + slp)


def _tp_sl_fixed_margin_pct(side: str, entry: float, tp_margin_pct: float, sl_margin_pct: float, lev: float) -> tuple[float, float]:
    """
    TP/SL so that ~tp_margin_pct / sl_margin_pct is the target margin P&L% at fill
    (same fee model as close: leveraged_pnl_pct ≈ price_move_pct * lev).
    Price move fraction = margin_pct / lev / 100.
    """
    lv = max(lev, 1e-9)
    tp_move = (tp_margin_pct / lv) / 100.0
    sl_move = (sl_margin_pct / lv) / 100.0
    if side == "LONG":
        return entry * (1.0 + tp_move), entry * (1.0 - sl_move)
    return entry * (1.0 - tp_move), entry * (1.0 + sl_move)


def _entry_filters_pass(snap: TASnapshot, side: str, df: pd.DataFrame) -> tuple[bool, str]:
    """
    Optional stricter gates when TA_SIGNAL_FILTERS=1.
    Returns (True, "") to allow entry, or (False, reason) to skip.
    """
    if not _signal_filters_enabled():
        return True, ""

    if _sf_sub("TA_SF_SCORE_FILTER", "1"):
        long_min = float(os.environ.get("TA_SF_LONG_MIN", "2.0"))
        short_max = float(os.environ.get("TA_SF_SHORT_MAX", "-2.0"))
        if side == "LONG" and snap.score_5m < long_min:
            return False, f"5m score {snap.score_5m:+.4f} < TA_SF_LONG_MIN ({long_min})"
        if side == "SHORT" and snap.score_5m > short_max:
            return False, f"5m score {snap.score_5m:+.4f} > TA_SF_SHORT_MAX ({short_max})"

    if _sf_sub("TA_SF_TREND_FILTER", "1"):
        adx_min = float(os.environ.get("TA_SF_ADX_MIN", "20"))
        adx, mhist = _adx_macd_from_df(df)
        if adx_min >= 0:
            if adx is None:
                return False, "ADX unavailable"
            if adx < adx_min:
                return False, f"ADX {adx:.1f} < TA_SF_ADX_MIN ({adx_min})"
        if _sf_sub("TA_SF_MACD_ALIGN", "1"):
            # Composite TA score can be Strong Buy/Sell while MACD hist still lags; optional bypass.
            strong_label_match = (side == "LONG" and snap.label_5m == "Strong Buy") or (
                side == "SHORT" and snap.label_5m == "Strong Sell"
            )
            bypass_macd = (
                _sf_sub("TA_SF_MACD_BYPASS_STRONG_5M", "0")
                or (
                    _sf_sub("TA_OPEN_EVERY_STRONG_5M_ONLY", "0") and strong_label_match
                )
            )
            if not bypass_macd:
                if mhist is None:
                    return False, "MACD histogram unavailable"
                if side == "LONG" and mhist <= 0:
                    return False, f"MACD hist {mhist:.6f} not bullish (≤0)"
                if side == "SHORT" and mhist >= 0:
                    return False, f"MACD hist {mhist:.6f} not bearish (≥0)"

    if _sf_sub("TA_SF_HTF_FILTER", "1"):
        h = snap.htf_scores
        if not h or "15m" not in h or "1h" not in h:
            return True, ""
        s15 = h["15m"]
        s1h = h["1h"]
        bearish_max = float(os.environ.get("TA_SF_HT_BEARISH_MAX", "-0.5"))
        bullish_min = float(os.environ.get("TA_SF_HT_BULLISH_MIN", "0.5"))
        if side == "LONG" and (s15 <= bearish_max or s1h <= bearish_max):
            return (
                False,
                f"HTF bearish vs LONG: 15m={s15:+.2f} 1h={s1h:+.2f} (≤ {bearish_max})",
            )
        if side == "SHORT" and (s15 >= bullish_min or s1h >= bullish_min):
            return (
                False,
                f"HTF bullish vs SHORT: 15m={s15:+.2f} 1h={s1h:+.2f} (≥ {bullish_min})",
            )

    return True, ""


def _fixed_tp_sl_levels(
    side: str,
    entry: float,
    tp_pct: float,
    sl_pct: float,
    lev: float,
    atr_5m: float | None,
) -> tuple[float, float, str]:
    """Returns (tp_price, sl_price, mode_label). mode: atr | margin | underlying."""
    use_atr = os.environ.get("TA_TP_SL_USE_ATR", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_atr and atr_5m is not None and atr_5m > 0:
        tpm = float(os.environ.get("TA_SIGNAL_TP_ATR_MULT", "2"))
        slm = float(os.environ.get("TA_SIGNAL_SL_ATR_MULT", "1"))
        if side == "LONG":
            return entry + tpm * atr_5m, entry - slm * atr_5m, "atr"
        return entry - tpm * atr_5m, entry + slm * atr_5m, "atr"
    use_margin = os.environ.get("TA_TP_SL_MARGIN_PCT", "1").strip().lower() in ("1", "true", "yes", "on")
    if use_margin:
        tp_p, sl_p = _tp_sl_fixed_margin_pct(side, entry, tp_pct, sl_pct, lev)
        return tp_p, sl_p, "margin"
    tp_p, sl_p = _tp_sl_fixed_price_pct(side, entry, tp_pct, sl_pct)
    return tp_p, sl_p, "underlying"


def _round_to_step(v: float, step: float, up: bool = False) -> float:
    if step <= 0:
        return float(v)
    q = Decimal(str(step))
    d = Decimal(str(v))
    n = d / q
    r = n.to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN) * q
    return float(r)


def _futures_symbol_filters(client: Client, symbol: str) -> tuple[float, float, float, float]:
    ex = client.futures_exchange_info()
    for s in ex.get("symbols", []):
        if str(s.get("symbol", "")).upper() != symbol.upper():
            continue
        tick = 0.0
        step = 0.0
        min_qty = 0.0
        min_notional = 20.0
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "PRICE_FILTER":
                tick = float(f.get("tickSize", "0") or 0)
            elif ft == "LOT_SIZE":
                step = float(f.get("stepSize", "0") or 0)
                min_qty = float(f.get("minQty", "0") or 0)
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                mn = f.get("notional") or f.get("minNotional") or "0"
                if float(mn or 0) > 0:
                    min_notional = float(mn)
        return tick, step, min_qty, min_notional
    raise ValueError(f"Futures symbol not found in exchange info: {symbol}")


def _futures_position_amt(client: Client, symbol: str) -> float:
    rows = client.futures_position_information(symbol=symbol)
    for r in rows:
        if str(r.get("symbol", "")).upper() == symbol.upper():
            return float(r.get("positionAmt", "0") or 0.0)
    return 0.0


def _futures_setup(client: Client, symbol: str, lev: int, isolated: bool = True) -> None:
    try:
        client.futures_change_position_mode(dualSidePosition="false")
    except Exception:
        pass
    try:
        client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED" if isolated else "CROSSED")
    except Exception:
        pass
    client.futures_change_leverage(symbol=symbol, leverage=max(1, int(lev)))


def _futures_available_usdt(client: Client) -> float:
    try:
        for r in client.futures_account_balance():
            if str(r.get("asset", "")).upper() == "USDT":
                return float(r.get("availableBalance", "0") or 0.0)
    except Exception:
        pass
    return 0.0


def _decide_ta_entry(snap: TASnapshot) -> tuple[str, float, float, float, float] | None:
    """
    Decide entry from current TA logic.
    Returns (side, close_price, tp_price, sl_price, lev) or None.
    """
    if snap.df_5m is None or len(snap.df_5m) < 60:
        return None
    df = snap.df_5m
    close_price = float(df["close"].iloc[-1])
    lev = float(os.environ.get("TA_LEVERAGE", "20"))
    long_min = float(os.environ.get("TA_LONG_ENTRY_SCORE", "0.8"))
    short_max = float(os.environ.get("TA_SHORT_ENTRY_SCORE", "-0.8"))
    side = ""
    tp_price = 0.0
    sl_price = 0.0
    open_every = os.environ.get("TA_OPEN_EVERY_DIGEST", "0").strip().lower() in ("1", "true", "yes", "on")
    price_tp_pct = float(os.environ.get("TA_TP_PRICE_PCT", "6"))
    price_sl_pct = float(os.environ.get("TA_SL_PRICE_PCT", "2.5"))
    atr_sig = _atr_from_df(df)
    use_gemini_live = _gemini_live_entries_enabled() and not open_every
    if open_every:
        sc5 = snap.score_5m
        lab5 = (snap.label_5m or "").strip()
        strong_5m_only = _sf_sub("TA_OPEN_EVERY_STRONG_5M_ONLY", "0")
        if strong_5m_only:
            if lab5 == "Strong Buy":
                side = "LONG"
            elif lab5 == "Strong Sell":
                side = "SHORT"
        else:
            min_abs = float(os.environ.get("TA_OPEN_EVERY_MIN_ABS_SCORE", "0") or 0.0)
            if min_abs > 0:
                if sc5 >= min_abs:
                    side = "LONG"
                elif sc5 <= -min_abs:
                    side = "SHORT"
            else:
                side = "LONG" if sc5 >= 0 else "SHORT"
    else:
        ms = snap.score_for_entry
        want_long = ms >= long_min
        want_short = ms <= short_max
        if want_long and not want_short:
            side = "LONG"
        elif want_short and not want_long:
            side = "SHORT"
    if not side and use_gemini_live:
        dec = None
        try:
            dec = run_gemini_decision(
                os.environ.get("TA_FUTURES_SYMBOL", os.environ.get("TA_SYMBOL", "ETHUSDC")).strip().upper(),
                close_price,
                snap.text,
                snap.tf_scores,
                snap.tf_labels,
                snap.score_for_entry,
                aggregate_score_label="5m score" if snap.entry_score_kind == "5m" else "Mean score",
            )
        except Exception as e:
            print(f"LIVE Gemini decision failed: {e} — falling back to TA score entry", flush=True)
        if dec:
            action = str(dec.get("action", "HOLD")).upper()
            if action in ("LONG", "SHORT"):
                tp_raw = dec.get("take_profit")
                sl_raw = dec.get("stop_loss")
                tp_v, sl_v = validate_tp_sl(action, close_price, tp_raw, sl_raw)
                side = action
                if tp_v is not None and sl_v is not None:
                    tp_price, sl_price = tp_v, sl_v
                else:
                    tp_price, sl_price, _ = _fixed_tp_sl_levels(
                        side, close_price, price_tp_pct, price_sl_pct, lev, atr_sig
                    )
    if not side:
        return None
    reverse_on = _reverse_signals_enabled()
    if reverse_on:
        if tp_price > 0 and sl_price > 0:
            side, tp_price, sl_price = _reverse_side_and_levels(side, close_price, tp_price, sl_price)
        else:
            side = _opposite_side(side)
    ok, _reason = _entry_filters_pass(snap, side, df)
    if not ok:
        return None
    if tp_price <= 0 or sl_price <= 0:
        tp_price, sl_price, _ = _fixed_tp_sl_levels(side, close_price, price_tp_pct, price_sl_pct, lev, atr_sig)
    return side, close_price, tp_price, sl_price, lev


def process_ta_trade_live_futures(symbol: str, snap: TASnapshot, token: str) -> None:
    """Live Binance USD-M futures execution (env-gated)."""
    fut_symbol = os.environ.get("TA_FUTURES_SYMBOL", symbol).strip().upper()
    client = _client()
    def _tx(msg: str) -> None:
        if token and recipient_chat_ids({}):
            broadcast_telegram_plain(token, msg, {})
        print(msg, flush=True)

    dec = _decide_ta_entry(snap)
    if dec is None:
        print("LIVE skip: no entry decision this cycle (signal/filter gate).", flush=True)
        return
    side, close_price, tp_price, sl_price, lev = dec
    # one-position-only
    pos_amt = _futures_position_amt(client, fut_symbol)
    if abs(pos_amt) > 1e-12:
        print(f"LIVE skip: existing futures position amount on {fut_symbol}: {pos_amt}", flush=True)
        return
    _futures_setup(client, fut_symbol, int(lev), isolated=True)
    tick, step, min_qty, min_notional = _futures_symbol_filters(client, fut_symbol)
    order_book = client.futures_order_book(symbol=fut_symbol, limit=5)
    bid = float(order_book["bids"][0][0])
    ask = float(order_book["asks"][0][0])
    maker_bps = float(os.environ.get("TA_REAL_ENTRY_MAKER_OFFSET_BPS", "1.0"))
    if side == "LONG":
        raw_px = min(close_price, bid) * (1.0 - maker_bps / 10000.0)
        px = _round_to_step(raw_px, tick, up=False)
        entry_side = "BUY"
    else:
        raw_px = max(close_price, ask) * (1.0 + maker_bps / 10000.0)
        px = _round_to_step(raw_px, tick, up=True)
        entry_side = "SELL"
    fixed_qty = float(os.environ.get("TA_REAL_FIXED_QTY", "0") or 0.0)
    min_notional_qty = (min_notional / max(px, 1e-12)) if min_notional > 0 else 0.0
    target_qty = fixed_qty if fixed_qty > 0 else max(min_qty, min_notional_qty)
    qty = _round_to_step(target_qty, step, up=True)
    if qty <= 0:
        print("LIVE skip: computed quantity is zero after step rounding.", flush=True)
        return
    order_notional = qty * px
    if min_notional > 0 and order_notional < min_notional:
        qty = _round_to_step(min_notional / max(px, 1e-12), step, up=True)
        order_notional = qty * px
    if min_notional > 0 and order_notional < min_notional:
        print(
            f"LIVE skip: notional still below exchange min after rounding "
            f"(notional={order_notional:.4f}, min={min_notional:.4f}).",
            flush=True,
        )
        return
    avail = _futures_available_usdt(client)
    req_margin = order_notional / max(float(lev), 1e-12)
    fee_buffer = order_notional * 0.0015  # conservative entry+exit + slippage buffer
    if avail > 0 and avail < (req_margin + fee_buffer):
        print(
            f"LIVE skip: insufficient available USDT for order "
            f"(avail={avail:.4f}, required~{req_margin + fee_buffer:.4f}).",
            flush=True,
        )
        return

    preplace = os.environ.get("TA_REAL_PREPLACE_EXITS", "1").strip().lower() in ("1", "true", "yes", "on")
    exit_mode = (os.environ.get("TA_REAL_EXIT_ORDER_MODE", "limit") or "limit").strip().lower()
    entry_id = f"ta_live_entry_{int(time.time())}"
    preplace_ok = False

    def _place_exit_orders(qty_for_exit: float) -> bool:
        exit_side = "SELL" if side == "LONG" else "BUY"
        tp_stop = _round_to_step(tp_price, tick, up=(side == "LONG"))
        sl_stop = _round_to_step(sl_price, tick, up=(side == "LONG"))
        if exit_mode == "market":
            client.futures_create_order(
                symbol=fut_symbol,
                side=exit_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=tp_stop,
                closePosition=True,
                workingType="MARK_PRICE",
            )
            client.futures_create_order(
                symbol=fut_symbol,
                side=exit_side,
                type="STOP_MARKET",
                stopPrice=sl_stop,
                closePosition=True,
                workingType="MARK_PRICE",
            )
            return True

        # Limit exits: trigger + resting limit order (reduceOnly).
        # Using price == stopPrice keeps behavior simple and explicit.
        client.futures_create_order(
            symbol=fut_symbol,
            side=exit_side,
            type="TAKE_PROFIT",
            quantity=qty_for_exit,
            price=tp_stop,
            stopPrice=tp_stop,
            timeInForce="GTC",
            reduceOnly=True,
            workingType="MARK_PRICE",
        )
        client.futures_create_order(
            symbol=fut_symbol,
            side=exit_side,
            type="STOP",
            quantity=qty_for_exit,
            price=sl_stop,
            stopPrice=sl_stop,
            timeInForce="GTC",
            reduceOnly=True,
            workingType="MARK_PRICE",
        )
        return True

    if preplace:
        try:
            _place_exit_orders(qty)
            preplace_ok = True
        except Exception:
            preplace_ok = False

    ord0 = client.futures_create_order(
        symbol=fut_symbol,
        side=entry_side,
        type="LIMIT",
        quantity=qty,
        price=px,
        timeInForce="GTC",
        newClientOrderId=entry_id,
    )
    _tx(
        f"📡 LIVE {side} LIMIT submitted\n"
        f"Symbol: {fut_symbol} | Qty: {qty} | Limit: {px:,.2f} | Notional: {order_notional:.2f}\n"
        f"Planned TP: {tp_price:,.2f} | SL: {sl_price:,.2f} | Leverage: {lev:.1f}x"
    )
    oid = int(ord0.get("orderId"))
    timeout_s = int(float(os.environ.get("TA_REAL_ENTRY_TIMEOUT_SEC", "20")))
    start = time.time()
    filled = False
    avg_fill = px
    while time.time() - start < timeout_s:
        st = client.futures_get_order(symbol=fut_symbol, orderId=oid)
        status = str(st.get("status", "")).upper()
        if status == "FILLED":
            filled = True
            ap = st.get("avgPrice")
            if ap:
                avg_fill = float(ap)
            break
        if status in ("CANCELED", "EXPIRED", "REJECTED"):
            break
        time.sleep(2)
    if not filled:
        try:
            client.futures_cancel_order(symbol=fut_symbol, orderId=oid)
        except Exception:
            pass
        # Pre-place may have added TP/SL; clear all open orders for this symbol (conditional + limit).
        if _sf_sub("TA_REAL_CANCEL_ALL_ON_ENTRY_TIMEOUT", "1"):
            try:
                client.futures_cancel_all_open_orders(symbol=fut_symbol)
            except Exception as e:
                print(f"LIVE: cancel_all_open_orders after entry timeout failed: {e}", flush=True)
        _tx("⚠️ LIVE entry not filled in time; entry canceled and open orders cleared for symbol.")
        return

    # Fallback: place exits now only if not already pre-placed.
    if not preplace_ok:
        try:
            _place_exit_orders(qty)
        except Exception as e:
            _tx(f"⚠️ LIVE entry filled but TP/SL placement failed: {e}")
            return
    _tx(
        f"✅ LIVE {side} opened\n"
        f"Entry fill: {avg_fill:,.2f}\n"
        f"TP/SL mode: {exit_mode.upper()} | TP: {tp_price:,.2f} | SL: {sl_price:,.2f}"
    )


def process_ta_trade_sim(symbol: str, snap: TASnapshot, token: str) -> None:
    """Paper trade from mean TA score; TP/SL/fees same as ML trader_simulation defaults."""
    if snap.df_5m is None or len(snap.df_5m) < 60:
        return

    lev = float(os.environ.get("TA_LEVERAGE", "20"))
    fee_bps = float(os.environ.get("TA_FEE_BPS_PER_SIDE", "4"))
    tp_mult = float(os.environ.get("TA_TP_ATR_MULT", "4.0"))
    sl_mult = float(os.environ.get("TA_SL_ATR_MULT", "2.5"))
    tp_pct = float(os.environ.get("TA_TP_PCT_FALLBACK", "0.15")) / 100.0
    sl_pct = float(os.environ.get("TA_SL_PCT_FALLBACK", "0.1")) / 100.0
    long_min = float(os.environ.get("TA_LONG_ENTRY_SCORE", "0.8"))
    short_max = float(os.environ.get("TA_SHORT_ENTRY_SCORE", "-0.8"))
    min_bars = int(os.environ.get("TA_MIN_BARS_BETWEEN_TRADES", "2"))

    df = snap.df_5m
    row = df.iloc[-1]
    high_price = float(row["high"])
    low_price = float(row["low"])
    close_price = float(row["close"])
    close_time = _bar_close_time_5m(df)

    pos = _load_position(symbol)
    balance_before, starting_balance = _load_balance(symbol)

    def _tx(msg: str) -> None:
        if token and recipient_chat_ids({}):
            broadcast_telegram_plain(token, msg, {})
        print(msg, flush=True)

    # --- manage open position ---
    if pos and pos.get("open"):
        side = pos["side"]
        entry = float(pos["entry_price"])
        tp_price = float(pos["tp_price"])
        sl_price = float(pos["sl_price"])
        hit_tp = hit_sl = False
        exit_price = close_price
        if side == "LONG":
            if high_price >= tp_price:
                hit_tp = True
                exit_price = tp_price
            elif low_price <= sl_price:
                hit_sl = True
                exit_price = sl_price
        else:
            if low_price <= tp_price:
                hit_tp = True
                exit_price = tp_price
            elif high_price >= sl_price:
                hit_sl = True
                exit_price = sl_price

        if hit_tp or hit_sl:
            if side == "LONG":
                profit = exit_price - entry
            else:
                profit = entry - exit_price
            profit_pct = 100.0 * profit / entry if entry else 0.0
            leveraged_pnl_pct = profit_pct * lev
            fee_margin_pct = 2 * (fee_bps / 10000.0) * lev * 100.0
            balance_after = balance_before * (1.0 + leveraged_pnl_pct / 100.0 - fee_margin_pct / 100.0)
            balance_after = max(0.01, balance_after)
            fee_usd = balance_before * (fee_margin_pct / 100.0)
            total_return_pct = 100.0 * (balance_after - starting_balance) / starting_balance if starting_balance else 0.0

            _save_balance(symbol, balance_after, starting_balance)
            _clear_position(symbol)
            _save_last_close(symbol, close_time)

            txp = _tx_path(symbol)
            txp.parent.mkdir(parents=True, exist_ok=True)
            t_line = f"{close_time},{exit_price:.2f},{profit:.2f},{'SELL' if side == 'LONG' else 'BUY'}\n"
            with open(txp, "a", encoding="utf-8") as f:
                f.write(t_line)

            res = "TP" if hit_tp else "SL"
            win = hit_tp
            emoji = "✅" if win else "❌"
            st0 = _load_stats(symbol)
            if win:
                st0["wins"] += 1
            else:
                st0["losses"] += 1
            _save_stats(symbol, st0["wins"], st0["losses"])
            closed_n = st0["wins"] + st0["losses"]

            _tx(
                f"🔒 {side} closed ({emoji} {res})\n"
                f"Entry: {entry:,.2f} → Exit: {exit_price:,.2f}\n"
                f"Price P&L: {profit_pct:+.2f}% → Margin: {leveraged_pnl_pct:+.2f}% | Fee: ${fee_usd:.2f}\n"
                f"Balance: ${balance_after:.2f} | Total return: {total_return_pct:+.1f}%\n\n"
                f"📊 Session stats\n"
                f"Wins: {st0['wins']} | Losses: {st0['losses']}\n"
                f"Balance: ${balance_after:.2f} (start ${starting_balance:.2f})\n"
                f"Total return: {total_return_pct:+.1f}%"
            )
        return

    # --- flat: cooldown ---
    last_c = _load_last_close(symbol)
    if last_c is not None and min_bars > 0:
        try:
            last_ts = pd.Timestamp(last_c)
            now_ts = pd.Timestamp(close_time)
            bars_since = (now_ts - last_ts) / pd.Timedelta(minutes=5)
            if bars_since < float(min_bars):
                return
        except Exception:
            pass

    # --- entry: TA_OPEN_EVERY_DIGEST (5m) > Gemini > fixed% + score_for_entry > ATR + score_for_entry ---
    open_every = os.environ.get("TA_OPEN_EVERY_DIGEST", "0").strip().lower() in ("1", "true", "yes", "on")
    entry_on_banner = os.environ.get("TA_ENTRY_ON_SIGNAL_BANNER", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    price_tp_pct = float(os.environ.get("TA_TP_PRICE_PCT", "6"))
    price_sl_pct = float(os.environ.get("TA_SL_PRICE_PCT", "2.5"))
    use_fixed_tp_sl = open_every or os.environ.get("TA_USE_FIXED_TP_SL_PCT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    use_gemini = _gemini_entries_env_enabled() and not open_every

    atr_raw = _atr_from_df(df)
    atr = atr_raw
    if atr is None or atr <= 0:
        atr = close_price * (tp_pct + sl_pct) / 2.0
    atr_sig = atr_raw if atr_raw is not None and atr_raw > 0 else None
    if (
        os.environ.get("TA_TP_SL_USE_ATR", "0").strip().lower() in ("1", "true", "yes", "on")
        and atr_sig is None
    ):
        print(
            "TA_TP_SL_USE_ATR=1 but ATR(14) on 5m is unavailable — using margin/underlying % for TP/SL",
            flush=True,
        )

    side: str = ""
    tp_price: float = 0.0
    sl_price: float = 0.0
    open_extra: str = ""
    gemini_note = ""
    opened_from_banner = False
    last_fixed_mode = ""

    tpm_atr = float(os.environ.get("TA_SIGNAL_TP_ATR_MULT", "2"))
    slm_atr = float(os.environ.get("TA_SIGNAL_SL_ATR_MULT", "1"))

    if open_every:
        sc5 = snap.score_5m
        lab5 = (snap.label_5m or "").strip()
        strong_5m_only = _sf_sub("TA_OPEN_EVERY_STRONG_5M_ONLY", "0")
        if strong_5m_only:
            if lab5 == "Strong Buy":
                side = "LONG"
            elif lab5 == "Strong Sell":
                side = "SHORT"
            else:
                side = ""
        else:
            try:
                min_abs = float(os.environ.get("TA_OPEN_EVERY_MIN_ABS_SCORE", "0"))
            except ValueError:
                min_abs = 0.0
            if min_abs > 0:
                if sc5 >= min_abs:
                    side = "LONG"
                elif sc5 <= -min_abs:
                    side = "SHORT"
                else:
                    side = ""
            else:
                side = "LONG" if sc5 >= 0 else "SHORT"
        if not side:
            return
        tp_price, sl_price, _tp_sl_mode = _fixed_tp_sl_levels(
            side, close_price, price_tp_pct, price_sl_pct, lev, atr_sig
        )
        last_fixed_mode = _tp_sl_mode
        if _tp_sl_mode == "atr" and atr_sig is not None:
            tp_sl_txt = (
                f"TP {tpm_atr}×ATR / SL {slm_atr}×ATR "
                f"(ATR(14)≈{atr_sig:.4f}, {tpm_atr:.1f}:{slm_atr:.1f} TP:SL on price)"
            )
        elif _tp_sl_mode == "margin":
            tp_sl_txt = (
                f"TP +{price_tp_pct}% / SL -{price_sl_pct}% on margin "
                f"(≈{price_tp_pct / lev:.3f}% / {price_sl_pct / lev:.3f}% ETH move @ {lev}x)"
            )
        else:
            tp_sl_txt = f"TP +{price_tp_pct}% / SL -{price_sl_pct}% (underlying price)"
        if strong_5m_only:
            open_extra = (
                f"5m label {lab5} (score {sc5:+.4f}) | Strong 5m only | {tp_sl_txt}"
            )
        else:
            open_extra = f"5m TA score {sc5:+.4f} | open each digest when flat | {tp_sl_txt}"
    elif entry_on_banner and not open_every:
        bs = _banner_entry_side(snap.banner)
        if bs:
            side = bs
            opened_from_banner = True
            tp_price, sl_price, _tp_sl_mode = _fixed_tp_sl_levels(
                side, close_price, price_tp_pct, price_sl_pct, lev, atr_sig
            )
            last_fixed_mode = _tp_sl_mode
            if _tp_sl_mode == "atr" and atr_sig is not None:
                tp_sl_txt = (
                    f"TP {tpm_atr}×ATR / SL {slm_atr}×ATR "
                    f"(ATR(14)≈{atr_sig:.4f}, {tpm_atr:.1f}:{slm_atr:.1f} TP:SL on price)"
                )
            elif _tp_sl_mode == "margin":
                tp_sl_txt = (
                    f"TP +{price_tp_pct}% / SL -{price_sl_pct}% on margin "
                    f"(≈{price_tp_pct / lev:.3f}% / {price_sl_pct / lev:.3f}% ETH move @ {lev}x)"
                )
            else:
                tp_sl_txt = f"TP +{price_tp_pct}% / SL -{price_sl_pct}% (underlying price)"
            open_extra = f"Multi-TF banner entry ({snap.banner}) | {tp_sl_txt}"

    if not side and use_gemini:
        gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not gemini_key:
            print("Gemini enabled but GEMINI_API_KEY is empty — falling back to TA score entry", flush=True)
        else:
            dec = None
            try:
                dec = run_gemini_decision(
                    symbol,
                    close_price,
                    snap.text,
                    snap.tf_scores,
                    snap.tf_labels,
                    snap.score_for_entry,
                    aggregate_score_label="5m score" if snap.entry_score_kind == "5m" else "Mean score",
                )
            except Exception as e:
                print(f"Gemini decision failed: {e} — falling back to TA score entry", flush=True)
            if dec:
                action = str(dec.get("action", "HOLD")).upper()
                if action in ("LONG", "SHORT"):
                    tp_raw = dec.get("take_profit")
                    sl_raw = dec.get("stop_loss")
                    tp_v, sl_v = validate_tp_sl(action, close_price, tp_raw, sl_raw)
                    if tp_v is not None and sl_v is not None:
                        side = action
                        tp_price, sl_price = tp_v, sl_v
                        reason = "gemini_prices"
                    else:
                        side = action
                        if action == "LONG":
                            tp_price = close_price + atr * tp_mult
                            sl_price = close_price - atr * sl_mult
                        else:
                            tp_price = close_price - atr * tp_mult
                            sl_price = close_price + atr * sl_mult
                        reason = "gemini_atr_fallback"
                    gemini_note = (dec.get("rationale") or "")[:500]
                    conf = dec.get("confidence", 0)
                    open_extra = (
                        f"Gemini conf={conf} | {reason}\n{gemini_note}"
                        if gemini_note
                        else f"Gemini conf={conf} | {reason}"
                    )
                else:
                    print(
                        f"Gemini action={action} — falling back to TA score entry if thresholds match",
                        flush=True,
                    )

    if not side and use_fixed_tp_sl:
        ms = snap.score_for_entry
        es = "5m" if snap.entry_score_kind == "5m" else "mean TF"
        want_long = ms >= long_min
        want_short = ms <= short_max
        if not want_long and not want_short:
            return
        if want_long and want_short:
            return
        side = "LONG" if want_long else "SHORT"
        tp_price, sl_price, _tp_sl_mode = _fixed_tp_sl_levels(
            side, close_price, price_tp_pct, price_sl_pct, lev, atr_sig
        )
        last_fixed_mode = _tp_sl_mode
        if _tp_sl_mode == "atr" and atr_sig is not None:
            open_extra = (
                f"{es} score {ms:+.2f} | ATR TP/SL {tpm_atr}×/{slm_atr}× "
                f"(ATR(14)≈{atr_sig:.4f}, {tpm_atr:.1f}:{slm_atr:.1f} TP:SL)"
            )
        elif _tp_sl_mode == "margin":
            open_extra = (
                f"{es} score {ms:+.2f} | fixed TP +{price_tp_pct}% SL -{price_sl_pct}% margin "
                f"(≈{price_tp_pct / lev:.3f}% / {price_sl_pct / lev:.3f}% ETH @ {lev}x)"
            )
        else:
            open_extra = f"{es} score {ms:+.2f} | fixed TP +{price_tp_pct}% SL -{price_sl_pct}% (underlying)"
    elif not side:
        ms = snap.score_for_entry
        es = "5m" if snap.entry_score_kind == "5m" else "mean TF"
        want_long = ms >= long_min
        want_short = ms <= short_max
        if not want_long and not want_short:
            return
        if want_long and want_short:
            return
        if want_long:
            side = "LONG"
            tp_price = close_price + atr * tp_mult
            sl_price = close_price - atr * sl_mult
            open_extra = f"{es} TA score {ms:+.2f} (ATR TP/SL)"
        else:
            side = "SHORT"
            tp_price = close_price - atr * tp_mult
            sl_price = close_price + atr * sl_mult
            open_extra = f"{es} TA score {ms:+.2f} (ATR TP/SL)"

    if not side:
        return

    if _reverse_signals_enabled():
        side, tp_price, sl_price = _reverse_side_and_levels(side, close_price, tp_price, sl_price)
        if open_extra:
            open_extra = f"{open_extra} | reversed signals"
        else:
            open_extra = "reversed signals"

    ok, skip_reason = _entry_filters_pass(snap, side, df)
    if not ok:
        print(f"TA-SIM entry skipped: {skip_reason}", flush=True)
        return

    pos_data = {
        "open": True,
        "side": side,
        "entry_price": close_price,
        "entry_time": str(close_time),
        "tp_price": tp_price,
        "sl_price": sl_price,
        "atr_at_entry": atr_sig if atr_sig is not None else atr,
    }
    if use_gemini and gemini_note:
        pos_data["gemini_rationale"] = gemini_note[:2000]

    _save_position(symbol, pos_data)
    emoji = "📈" if side == "LONG" else "📉"
    if open_every or opened_from_banner or (use_fixed_tp_sl and not use_gemini):
        if last_fixed_mode == "atr" and atr_sig is not None:
            meta = (
                f"ATR(14)≈{atr_sig:.4f} | TP {tpm_atr}×ATR / SL {slm_atr}×ATR ({tpm_atr:.1f}:{slm_atr:.1f} TP:SL) | "
                f"Leverage {lev}x | Balance ${balance_before:.2f}\nFees: {fee_bps} bps/side (margin-style)"
            )
        else:
            meta = f"Leverage {lev}x | Balance ${balance_before:.2f}\nFees: {fee_bps} bps/side (margin-style)"
    else:
        meta = f"ATR(14)≈{atr:.4f} | Leverage {lev}x | Balance ${balance_before:.2f}\nFees: {fee_bps} bps/side (margin-style)"
    _tx(
        f"{emoji} TA-SIM {side} opened\n"
        f"{open_extra}\n"
        f"Price: {close_price:,.2f}\n"
        f"TP: {tp_price:,.2f} | SL: {sl_price:,.2f}\n"
        f"{meta}"
    )


def _build_gemini_signal_block(symbol: str, snap: TASnapshot) -> str:
    """Optional digest section: Gemini direction + execution levels each cycle."""
    if snap.df_5m is None or len(snap.df_5m) < 2:
        return "🤖 Gemini signal: unavailable (insufficient 5m data)"
    close_price = float(snap.df_5m["close"].iloc[-1])
    try:
        dec = run_gemini_decision(
            symbol,
            close_price,
            snap.text,
            snap.tf_scores,
            snap.tf_labels,
            snap.score_for_entry,
            aggregate_score_label="5m score" if snap.entry_score_kind == "5m" else "Mean score",
        )
    except Exception as e:
        return f"🤖 Gemini signal: unavailable ({e})"
    if not dec:
        return "🤖 Gemini signal: unavailable (empty response)"
    action = str(dec.get("action", "HOLD")).upper()
    direction = str(dec.get("direction", "") or "").strip() or ("Neutral" if action == "HOLD" else action.title())
    conviction = int(dec.get("conviction_score", 0) or 0)
    conf = int(dec.get("confidence", 0) or 0)
    el = dec.get("entry_low")
    eh = dec.get("entry_high")
    tp = dec.get("take_profit")
    sl = dec.get("stop_loss")
    tp2 = dec.get("tp2")
    inv = str(dec.get("invalidation_point", "") or "").strip()
    rw = str(dec.get("risk_warning", "") or "").strip()
    entry_txt = f"{el:,.2f}-{eh:,.2f}" if isinstance(el, (int, float)) and isinstance(eh, (int, float)) else "n/a"
    tp2_txt = f"{tp2:,.2f}" if isinstance(tp2, (int, float)) else "n/a"
    line = (
        f"🤖 Gemini (Master Prompt)\n"
        f"Direction: {direction} ({action}) | Conviction: {conviction}/10 | Confidence: {conf}/100\n"
        f"Entry Zone: {entry_txt}\n"
        f"SL: {sl if isinstance(sl, (int, float)) else 'n/a'} | TP1: {tp if isinstance(tp, (int, float)) else 'n/a'} | TP2: {tp2_txt}"
    )
    if inv:
        line += f"\nInvalidation: {inv}"
    if rw:
        line += f"\nRisk Warning: {rw}"
    return line


def main() -> int:
    _load_project_dotenv()
    # Default matches ecosystem.config.cjs eth-ta-telegram; set TA_PRESET=none to use only explicit TA_* from .env
    os.environ.setdefault("TA_PRESET", "high-win-rate")
    _apply_ta_preset()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    trade_sim = _ta_trade_sim_enabled()
    trade_live = _ta_real_trading_enabled()

    if trade_sim and trade_live:
        print("Both TA_TRADE_SIM and TA_REAL_TRADING are enabled; disabling paper mode in favor of real mode.", flush=True)
        trade_sim = False

    if not trade_sim and not trade_live and not token:
        print("Set TELEGRAM_BOT_TOKEN or enable TA_TRADE_SIM=1 / TA_REAL_TRADING=1", file=sys.stderr)
        return 1
    if not trade_sim and not trade_live and not recipient_chat_ids({}):
        print("No Telegram recipients (subscribers file or TELEGRAM_CHAT_ID).", file=sys.stderr)
        return 1

    symbol = os.environ.get("TA_SYMBOL", "ETHUSDC").strip().upper()
    interval_sec = int(os.environ.get("TA_INTERVAL_SEC", "300"))
    limit = int(os.environ.get("TA_KLINES_LIMIT", "500"))

    reset_env = (
        os.environ.get("TA_RESET_ON_START", "0").strip().lower() in ("1", "true", "yes", "on")
        or os.environ.get("TA_RESET_BALANCE_ON_RESTART", "0").strip().lower() in ("1", "true", "yes", "on")
    )
    if reset_env:
        st = float(os.environ.get("TA_STARTING_BALANCE", "10"))
        _save_balance(symbol, st, st)
        _clear_position(symbol)
        _clear_last_close(symbol)
        _save_stats(symbol, 0, 0)
        print(
            f"TA reset on start: balance={st}, position + stats + inter-trade cooldown cleared "
            f"(TA_RESET_ON_START / TA_RESET_BALANCE_ON_RESTART)",
            flush=True,
        )

    preset_line = _env_preset_name()
    print(
        f"eth_ta_telegram: symbol={symbol} every {interval_sec}s trade_sim={trade_sim} "
        f"trade_live={trade_live} "
        f"TA_PRESET={preset_line or '(none)'} "
        f"gemini_entries={_gemini_entries_env_enabled()} (off when TA_OPEN_EVERY_DIGEST=1)",
        flush=True,
    )
    print(
        f"env check: TA_TRADE_SIM={repr(os.environ.get('TA_TRADE_SIM'))} "
        f"TA_TRADE_SIM_ENABLED={repr(os.environ.get('TA_TRADE_SIM_ENABLED'))} "
        f"TA_TRADE_ENABLED={repr(os.environ.get('TA_TRADE_ENABLED'))}",
        flush=True,
    )
    if not trade_sim and not trade_live:
        print(
            "TA_TRADE_SIM is off — no TA-SIM open/close messages. "
            "Set TA_TRADE_SIM=1 (or TA_TRADE_SIM_ENABLED / TA_TRADE_ENABLED) in project .env, then pm2 restart eth-ta-telegram --update-env. "
            "If it stays off, run: pm2 delete eth-ta-telegram && pm2 start ecosystem.config.cjs --only eth-ta-telegram",
            flush=True,
        )

    if (
        os.environ.get("TA_STARTUP_TELEGRAM", "1").strip().lower() in ("1", "true", "yes", "on")
        and token
        and recipient_chat_ids({})
    ):
        now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st_bal = float(os.environ.get("TA_STARTING_BALANCE", "10"))
        reset_line = ""
        if reset_env:
            reset_line = (
                f"\nReset on start: balance/position/stats/cooldown cleared → ${st_bal:.2f} start."
            )
        startup_msg = (
            "🟢 eth-ta-telegram started\n"
            f"As of {now_s}\n"
            f"Symbol: {symbol} | Loop: {interval_sec}s\n"
            f"TA_TRADE_SIM={trade_sim} | TA_REAL_TRADING={trade_live} | Gemini entries={_gemini_entries_env_enabled()} | "
            f"TA_SIGNAL_FILTERS={_signal_filters_enabled()}"
            f"{reset_line}"
        )
        try:
            n0 = broadcast_telegram_plain(token, startup_msg, {})
            print(f"Startup Telegram sent to {n0} chat(s)", flush=True)
        except Exception as e:
            print(f"Startup Telegram failed: {e}", file=sys.stderr, flush=True)

    while True:
        try:
            snap = build_snapshot(symbol, limit)
            msg = snap.text
            if snap.banner:
                msg = snap.banner + "\n\n" + msg
            if _gemini_signal_digest_enabled():
                msg = msg + "\n\n---\n" + _build_gemini_signal_block(symbol, snap)
            if trade_sim:
                process_ta_trade_sim(symbol, snap, token)
            elif trade_live:
                process_ta_trade_live_futures(symbol, snap, token)
            elif not _suppress_trade_sim_digest_hint():
                msg += (
                    "\n\n---\n"
                    "TA paper trading is OFF (set TA_TRADE_SIM=1, or TA_TRADE_SIM_ENABLED / TA_TRADE_ENABLED). "
                    "No TA-SIM entry/TP/SL messages are sent. "
                    "Set TA_TRADE_SIM=1 in project .env, then pm2 restart eth-ta-telegram --update-env"
                )
            if token and recipient_chat_ids({}):
                n = broadcast_telegram_plain(token, msg, {})
                print(f"{datetime.now(timezone.utc).isoformat()} digest sent to {n} chat(s)", flush=True)
            elif trade_sim:
                print(f"{datetime.now(timezone.utc).isoformat()} digest skipped (no Telegram)", flush=True)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr, flush=True)
        time.sleep(interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
