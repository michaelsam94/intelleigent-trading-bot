#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_eth_ta() -> Any:
    p = ROOT / "scripts" / "eth_ta_telegram.py"
    name = "eth_ta_digest_history"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _interval_ms(interval: str) -> int:
    m = {
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "30m": 30 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
        "1w": 7 * 24 * 60 * 60 * 1000,
        "1M": 30 * 24 * 60 * 60 * 1000,
    }
    return m[interval]


def _fetch_klines_range(client: Any, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    out: list = []
    cur = start_ms
    while cur < end_ms:
        batch = client.get_klines(
            symbol=symbol,
            interval=interval,
            startTime=cur,
            endTime=end_ms,
            limit=1000,
        )
        if not batch:
            break
        out.extend(batch)
        cur = int(batch[-1][0]) + 1
        if len(batch) < 1000:
            break
    return out


def _fmt_price(x: float) -> str:
    return f"{x:,.2f}"


def _build_digest_for_time(eth: Any, symbol: str, end_ts: pd.Timestamp, frames: dict[str, pd.DataFrame]) -> str | None:
    order = [
        ("5m", "5 Min"),
        ("15m", "15 Min"),
        ("30m", "30 Min"),
        ("1h", "Hourly"),
        ("1d", "Daily"),
        ("1w", "Weekly"),
        ("1M", "Monthly"),
    ]

    lines: list[str] = []
    now = end_ts.tz_convert(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"📊 TA digest — {symbol} (Binance spot)")
    lines.append(f"As of {now}")
    lines.append("")

    tf_scores: list[float] = []
    tf_labels: list[str] = []
    df_5m_slice: pd.DataFrame | None = None

    for interval, label in order:
        dfx = frames.get(interval)
        if dfx is None:
            continue
        sl = dfx.loc[dfx["close_time"] <= end_ts]
        if len(sl) < 60:
            continue
        if interval == "5m":
            df_5m_slice = sl.copy()
        sc, det = eth._analyze_ohlcv(sl)
        lab = eth._tf_label(float(sc))
        tf_scores.append(float(sc))
        tf_labels.append(lab)
        lines.append(f"── {label} ──  {lab}")
        close_p = float(sl["close"].iloc[-1])
        lines.append(f"  Close: {_fmt_price(close_p)}")
        for k in sorted(det.keys()):
            lines.append(f"  {k}: {det[k]}")
        lines.append("")

    if not tf_scores or df_5m_slice is None:
        return None

    # Pivot from previous daily candle.
    d1 = frames.get("1d")
    if d1 is not None:
        sl_d = d1.loc[d1["close_time"] <= end_ts]
        if len(sl_d) >= 2:
            pv = eth._pivot_classic(sl_d.iloc[-2])
            lines.append("── Pivot (Classic, prev daily) ──")
            for k in ("R3", "R2", "R1", "P", "S1", "S2", "S3"):
                lines.append(f"  {k}: {_fmt_price(float(pv[k]))}")
            lines.append("")

    mean_score = float(sum(tf_scores) / len(tf_scores))
    score_5m = float(tf_scores[0])
    label_5m = tf_labels[0]
    lines.append(f"Summary (mean TF score): {eth._tf_label(mean_score)}")
    lines.append(f"5m score: {score_5m:+.4f} | TF labels: {', '.join(tf_labels)}")
    lines.append(f"Entry signal (5m TF): {label_5m} (score {score_5m:+.4f})")

    # Optional 30_MAR score line from already-built TF scores (no extra API calls).
    mtf: dict[str, float] = {"5m": score_5m}
    map_idx = {"15m": 1, "30m": 2, "1h": 3, "1d": 4, "1w": 5}
    for k, idx in map_idx.items():
        if len(tf_scores) > idx:
            mtf[k] = float(tf_scores[idx])
    c_bear = sum(1 for k in ("5m", "15m", "30m", "1h", "1d") if k in mtf and mtf[k] <= -2.0)
    c_bull = sum(1 for k in ("5m", "15m", "30m", "1h", "1d") if k in mtf and mtf[k] >= 2.0)
    adx_d = None
    d1 = frames.get("1d")
    if d1 is not None:
        sl_d = d1.loc[d1["close_time"] <= end_ts]
        if len(sl_d) >= 60:
            adx_d = eth._scalar_adx_df(sl_d)
    adx_txt = f" ADX1d={adx_d:.1f}" if adx_d is not None else ""
    lines.append("")
    lines.append(f"── 30_MAR MTF ──  bear≤-2: {c_bear} TF | bull≥+2: {c_bull} TF{adx_txt}")
    parts = [f"{k}={mtf[k]:+.2f}" for k in sorted(mtf.keys())]
    lines.append("  " + " | ".join(parts))

    # Signal banner.
    if label_5m in ("Strong Buy", "Buy"):
        return "📌 TA SIGNAL: BULLISH (5m TF)\n\n" + "\n".join(lines)
    if label_5m in ("Strong Sell", "Sell"):
        return "📌 TA SIGNAL: BEARISH (5m TF)\n\n" + "\n".join(lines)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate TA digest text log for last N days from Binance.")
    ap.add_argument("--days", type=int, default=2, help="How many days back to generate (default: 2)")
    ap.add_argument("--symbol", default="ETHUSDC", help="Binance spot symbol (default: ETHUSDC)")
    ap.add_argument(
        "--out",
        default="data/eth_ta_history.log",
        help="Output file path (default: data/eth_ta_history.log)",
    )
    ap.add_argument(
        "--step-min",
        type=int,
        default=5,
        help="Digest step minutes; 5 matches bot loop (default: 5)",
    )
    args = ap.parse_args()

    eth = _load_eth_ta()
    eth._load_project_dotenv()
    symbol = args.symbol.strip().upper()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=max(1, args.days))
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    client = eth._client()
    need = {
        "5m": max(args.days * 24 * 12 + 120, 500),
        "15m": max(args.days * 24 * 4 + 120, 300),
        "30m": max(args.days * 24 * 2 + 120, 240),
        "1h": max(args.days * 24 + 120, 200),
        "1d": max(args.days + 180, 220),
        "1w": 140,
        "1M": 100,
    }

    frames: dict[str, pd.DataFrame] = {}
    for interval, bars in need.items():
        back_start_ms = end_ms - int(bars) * _interval_ms(interval)
        kl = _fetch_klines_range(client, symbol, interval, back_start_ms, end_ms)
        if not kl or len(kl) < 60:
            continue
        frames[interval] = eth._klines_to_df(kl)
        # Ensure close_time is datetime for filtering.
        if "close_time" in frames[interval].columns and not pd.api.types.is_datetime64_any_dtype(
            frames[interval]["close_time"]
        ):
            frames[interval]["close_time"] = pd.to_datetime(frames[interval]["close_time"], unit="ms", utc=True)

    if "5m" not in frames:
        print("No 5m data available from Binance.")
        return 1

    df5 = frames["5m"]
    start_ts = pd.Timestamp(start_dt)
    all_lines: list[str] = []
    # Iterate by 5m bars. For example: step-min=5 -> 1 bar, 15 -> 3 bars.
    step = max(1, int(args.step_min // 5))

    for i in range(60, len(df5), step):
        end_ts = df5["close_time"].iloc[i]
        if end_ts < start_ts:
            continue
        msg = _build_digest_for_time(eth, symbol, end_ts, frames)
        if msg:
            all_lines.append(msg)

    out_path.write_text("\n\n".join(all_lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(all_lines)} digests to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
