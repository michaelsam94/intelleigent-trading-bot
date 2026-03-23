#!/usr/bin/env python3
"""
Multi-timeframe technical analysis digest for ETH (or any Binance spot symbol), sent to Telegram every N seconds.

Uses TA-Lib + Binance public klines. Recipients: same as trading bot (TELEGRAM_BOT_TOKEN + subscribers / TELEGRAM_CHAT_ID).

Env:
  TA_SYMBOL=ETHUSDC              # default
  TA_INTERVAL_SEC=300             # 5 minutes
  TA_KLINES_LIMIT=500
  TA_SIGNAL_ALERTS=1             # extra line when consensus is strong buy/sell across TFs
  TELEGRAM_BOT_TOKEN=...

Run:
  python -u scripts/eth_ta_telegram.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import talib

from binance import Client
from binance.exceptions import BinanceAPIException

from common.telegram_broadcast import broadcast_telegram_plain, recipient_chat_ids

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


def _tf_label(score: float) -> str:
    """Map aggregate score to TradingView-style label."""
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
    """Return (net_score, buy_count, sell_count) for price vs MAs."""
    b, s = 0, 0
    for _name, ma in mas.items():
        if ma is None or ma <= 0:
            continue
        if close > ma:
            b += 1
        elif close < ma:
            s += 1
    net = (b - s) * 0.25  # weight MAs
    return net, b, s


def _analyze_ohlcv(df: pd.DataFrame) -> tuple[float, dict[str, str]]:
    """
    Return (score, detail_strings) for last bar.
    Score roughly in [-4, 4] for voting.
    """
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(close)
    details: dict[str, str] = {}
    if n < 60:
        return 0.0, {"error": "not enough bars"}

    score = 0.0

    # Moving averages (SMA + EMA)
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

    # RSI(14)
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

    # MACD
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

    # Stochastic
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

    # ATR — volatility tag (not directional)
    atr = _last(talib.ATR(high, low, close, timeperiod=14))
    if atr is not None:
        rel = atr / c * 100.0 if c else 0.0
        details["ATR(14)"] = f"{atr:.4f} ({rel:.2f}% of price) Volatility"

    # ADX + DI
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


def build_report(symbol: str, limit: int) -> tuple[str, str | None]:
    """
    Returns (full_message, optional_signal_banner).
    """
    client = _client()
    frames = [
        ("5m", "5 Min"),
        ("15m", "15 Min"),
        ("30m", "30 Min"),
        ("1h", "Hourly"),
        ("1d", "Daily"),
        ("1w", "Weekly"),
        ("1M", "Monthly"),
    ]
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"📊 TA digest — {symbol} (Binance spot)")
    lines.append(f"As of {now}")
    lines.append("")

    tf_scores: list[float] = []
    tf_labels: list[str] = []

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

    # Pivot from previous daily candle
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

    # Overall summary
    if tf_scores:
        avg = float(np.mean(tf_scores))
        overall = _tf_label(avg)
        lines.append(f"Summary (mean TF score): {overall}")
        lines.append(f"TF labels: {', '.join(tf_labels)}")
    else:
        overall = "N/A"

    signal_banner: str | None = None
    if os.environ.get("TA_SIGNAL_ALERTS", "1").strip().lower() in ("1", "true", "yes", "on"):
        strong_buy = sum(1 for x in tf_labels if x == "Strong Buy")
        strong_sell = sum(1 for x in tf_labels if x == "Strong Sell")
        buyish = sum(1 for x in tf_labels if x in ("Strong Buy", "Buy"))
        sellish = sum(1 for x in tf_labels if x in ("Strong Sell", "Sell"))
        thr = int(os.environ.get("TA_SIGNAL_MIN_TF", "4"))
        if strong_buy >= 2 or buyish >= thr:
            signal_banner = "📌 TA SIGNAL: BULLISH (multi-TF alignment)"
        elif strong_sell >= 2 or sellish >= thr:
            signal_banner = "📌 TA SIGNAL: BEARISH (multi-TF alignment)"

    return "\n".join(lines), signal_banner


def main() -> int:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 1
    if not recipient_chat_ids({}):
        print("No Telegram recipients (subscribers file or TELEGRAM_CHAT_ID).", file=sys.stderr)
        return 1

    symbol = os.environ.get("TA_SYMBOL", "ETHUSDC").strip().upper()
    interval_sec = int(os.environ.get("TA_INTERVAL_SEC", "300"))
    limit = int(os.environ.get("TA_KLINES_LIMIT", "500"))

    print(f"eth_ta_telegram: symbol={symbol} every {interval_sec}s", flush=True)

    while True:
        try:
            msg, sig = build_report(symbol, limit)
            if sig:
                msg = sig + "\n\n" + msg
            n = broadcast_telegram_plain(token, msg, {})
            print(f"{datetime.now(timezone.utc).isoformat()} sent to {n} chat(s)", flush=True)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr, flush=True)
        time.sleep(interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
