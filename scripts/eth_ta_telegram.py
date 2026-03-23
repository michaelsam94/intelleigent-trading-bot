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

Env (TA trade sim — set TA_TRADE_SIM=1):
  TA_STARTING_BALANCE=10
  TA_LEVERAGE=20
  TA_FEE_BPS_PER_SIDE=4
  TA_TP_ATR_MULT=4.0
  TA_SL_ATR_MULT=2.5
  TA_TP_PCT_FALLBACK=0.15
  TA_SL_PCT_FALLBACK=0.1
  TA_LONG_ENTRY_SCORE=0.8      # mean TF score >= this → open LONG
  TA_SHORT_ENTRY_SCORE=-0.8    # mean TF score <= this → open SHORT
  TA_MIN_BARS_BETWEEN_TRADES=1 # 5m bars after a close before new entry
  TA_STATE_DIR=data/ta_sim     # isolated from ML trader position.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
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
    df_5m: pd.DataFrame | None


def build_snapshot(symbol: str, limit: int) -> TASnapshot:
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

    mean_score = float(np.mean(tf_scores)) if tf_scores else 0.0
    if tf_scores:
        overall = _tf_label(mean_score)
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

    return TASnapshot(
        text="\n".join(lines),
        banner=signal_banner,
        tf_scores=tf_scores,
        tf_labels=tf_labels,
        mean_score=mean_score,
        df_5m=df_5m,
    )


# --- TA paper trading (isolated paths) ---


def _ta_dir(symbol: str) -> Path:
    base = os.environ.get("TA_STATE_DIR", "data/ta_sim").strip()
    return _ROOT / base / symbol


def _pos_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "position.json"


def _bal_path(symbol: str) -> Path:
    return _ta_dir(symbol) / "balance.json"


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
    min_bars = int(os.environ.get("TA_MIN_BARS_BETWEEN_TRADES", "1"))

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
            _tx(
                f"🔒 TA-SIM {side} closed ({emoji} {res})\n"
                f"Entry: {entry:,.2f} → Exit: {exit_price:,.2f}\n"
                f"Price P&L: {profit_pct:+.2f}% → Margin: {leveraged_pnl_pct:+.2f}% | Est. fee: ${fee_usd:.2f}\n"
                f"Balance: ${balance_after:.2f} | Total return: {total_return_pct:+.1f}%\n"
                f"Mean TA score at entry context: {snap.mean_score:+.2f}"
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

    # --- entry from mean TA score ---
    ms = snap.mean_score
    want_long = ms >= long_min
    want_short = ms <= short_max
    if not want_long and not want_short:
        return
    if want_long and want_short:
        return

    atr = _atr_from_df(df)
    if atr is None or atr <= 0:
        atr = close_price * (tp_pct + sl_pct) / 2.0

    if want_long:
        side = "LONG"
        tp_price = close_price + atr * tp_mult
        sl_price = close_price - atr * sl_mult
    else:
        side = "SHORT"
        tp_price = close_price - atr * tp_mult
        sl_price = close_price + atr * sl_mult

    _save_position(
        symbol,
        {
            "open": True,
            "side": side,
            "entry_price": close_price,
            "entry_time": str(close_time),
            "tp_price": tp_price,
            "sl_price": sl_price,
            "atr_at_entry": atr,
        },
    )
    emoji = "📈" if side == "LONG" else "📉"
    _tx(
        f"{emoji} TA-SIM {side} opened (mean score {ms:+.2f})\n"
        f"Price: {close_price:,.2f}\n"
        f"TP: {tp_price:,.2f} | SL: {sl_price:,.2f}\n"
        f"ATR(14)≈{atr:.4f} | Leverage {lev}x | Balance ${balance_before:.2f}\n"
        f"Fees: {fee_bps} bps/side (open+close on notional, margin-style)"
    )


def main() -> int:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    trade_sim = os.environ.get("TA_TRADE_SIM", "0").strip().lower() in ("1", "true", "yes", "on")

    if not trade_sim and not token:
        print("Set TELEGRAM_BOT_TOKEN or enable TA_TRADE_SIM=1", file=sys.stderr)
        return 1
    if not trade_sim and not recipient_chat_ids({}):
        print("No Telegram recipients (subscribers file or TELEGRAM_CHAT_ID).", file=sys.stderr)
        return 1

    symbol = os.environ.get("TA_SYMBOL", "ETHUSDC").strip().upper()
    interval_sec = int(os.environ.get("TA_INTERVAL_SEC", "300"))
    limit = int(os.environ.get("TA_KLINES_LIMIT", "500"))

    if os.environ.get("TA_RESET_ON_START", "0").strip() in ("1", "true", "yes"):
        st = float(os.environ.get("TA_STARTING_BALANCE", "10"))
        _save_balance(symbol, st, st)
        _clear_position(symbol)
        print(f"TA_RESET_ON_START: balance reset to {st}", flush=True)

    print(
        f"eth_ta_telegram: symbol={symbol} every {interval_sec}s trade_sim={trade_sim}",
        flush=True,
    )

    while True:
        try:
            snap = build_snapshot(symbol, limit)
            msg = snap.text
            if snap.banner:
                msg = snap.banner + "\n\n" + msg
            if trade_sim:
                process_ta_trade_sim(symbol, snap, token)
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
