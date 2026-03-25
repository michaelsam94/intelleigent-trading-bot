#!/usr/bin/env python3
"""
Backtest the TA signal strategy used by TA-SIM (scripts/eth_ta_telegram.py).

Uses Binance spot klines, the same _analyze_ohlcv score, TP/SL (_fixed_tp_sl_levels),
fees and leverage as the live paper trader.

Examples:
  python scripts/backtest_ta_signals.py --days 30 --initial 100 --leverage 20
  python scripts/backtest_ta_signals.py --days 14 --initial 10 --leverage 20 --mode threshold
  python scripts/backtest_ta_signals.py --days 30 --initial 100 --leverage 20 --no-filters
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_eth_ta():
    p = _ROOT / "scripts" / "eth_ta_telegram.py"
    name = "eth_ta_telegram_bt"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Required on Python 3.12+: @dataclass resolves cls.__module__ via sys.modules during exec_module.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_close_time(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "close_time" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["close_time"]):
        out["close_time"] = pd.to_datetime(out["close_time"], unit="ms", utc=True)
    return out


def _fetch_klines_range(client, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    out: list = []
    cur = start_ms
    while cur < end_ms:
        batch = client.get_klines(symbol=symbol, interval=interval, startTime=cur, endTime=end_ms, limit=1000)
        if not batch:
            break
        out.extend(batch)
        cur = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    return out


def _precompute_htf_for_5m(
    df_5m: pd.DataFrame,
    df_hi: pd.DataFrame,
    analyze,
) -> list[float | None]:
    """For each 5m bar index i, TA score on all higher-TF candles closed by that bar's end."""
    df_hi = _ensure_close_time(df_hi)
    n = len(df_5m)
    scores: list[float | None] = [None] * n
    ct = df_hi["close_time"].values
    for i in range(n):
        end_ts = df_5m["timestamp"].iloc[i] + pd.Timedelta(minutes=5)
        mask = df_hi["close_time"] <= end_ts
        sl = df_hi.loc[mask]
        if len(sl) < 60:
            continue
        sc, _ = analyze(sl)
        scores[i] = float(sc)
    return scores


def _build_snap(
    eth_ta,
    score_5m: float,
    df_5m_slice: pd.DataFrame,
    htf: dict[str, float],
) -> object:
    lab = eth_ta._tf_label(score_5m)
    return eth_ta.TASnapshot(
        text="",
        banner=None,
        tf_scores=[score_5m],
        tf_labels=[lab],
        mean_score=score_5m,
        score_5m=score_5m,
        score_for_entry=score_5m,
        entry_score_kind="5m",
        label_5m=lab,
        df_5m=df_5m_slice,
        htf_scores=htf,
    )


def _apply_balance(prev: float, profit_pct: float, lev: float, fee_bps: float) -> float:
    leveraged_pnl_pct = profit_pct * lev
    fee_margin_pct = 2.0 * (fee_bps / 10000.0) * lev * 100.0
    nxt = prev * (1.0 + leveraged_pnl_pct / 100.0 - fee_margin_pct / 100.0)
    return max(0.01, nxt)


def run_backtest(args: argparse.Namespace) -> dict:
    eth_ta = _load_eth_ta()
    eth_ta._load_project_dotenv()
    # Default: TA_SIGNAL_FILTERS on (matches stricter TA-SIM). --no-filters overrides.

    # CLI overrides env for this run
    if args.symbol:
        os.environ["TA_SYMBOL"] = args.symbol.upper()
    os.environ["TA_LEVERAGE"] = str(args.leverage)
    os.environ["TA_FEE_BPS_PER_SIDE"] = str(args.fee_bps)
    os.environ["TA_TP_PRICE_PCT"] = str(args.tp_price_pct)
    os.environ["TA_SL_PRICE_PCT"] = str(args.sl_price_pct)
    os.environ["TA_MIN_BARS_BETWEEN_TRADES"] = str(args.min_bars)
    os.environ["TA_SIGNAL_ON_5M"] = "1"
    if args.use_atr:
        os.environ["TA_TP_SL_USE_ATR"] = "1"
    else:
        os.environ["TA_TP_SL_USE_ATR"] = "0"
    os.environ["TA_TP_SL_MARGIN_PCT"] = "1" if args.margin_tp_sl else "0"
    if args.filters:
        os.environ["TA_SIGNAL_FILTERS"] = "1"
    else:
        os.environ["TA_SIGNAL_FILTERS"] = "0"

    symbol = os.environ.get("TA_SYMBOL", "ETHUSDC").strip().upper()
    client = eth_ta._client()
    end_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start_ms = end_ms - int(args.days * 86400 * 1000)

    raw = _fetch_klines_range(client, symbol, "5m", start_ms, end_ms)
    if len(raw) < 80:
        raise ValueError(f"Not enough 5m klines ({len(raw)}). Check symbol and days.")

    df = eth_ta._klines_to_df(raw)
    df = _ensure_close_time(df)
    n = len(df)
    warmup = 60

    sc15 = sc1h = None
    if args.filters:
        r15 = _fetch_klines_range(client, symbol, "15m", start_ms - 7 * 86400 * 1000, end_ms)
        r1h = _fetch_klines_range(client, symbol, "1h", start_ms - 30 * 86400 * 1000, end_ms)
        if len(r15) >= 60:
            df_15 = _ensure_close_time(eth_ta._klines_to_df(r15))
            sc15 = _precompute_htf_for_5m(df, df_15, eth_ta._analyze_ohlcv)
        if len(r1h) >= 60:
            df_1h = _ensure_close_time(eth_ta._klines_to_df(r1h))
            sc1h = _precompute_htf_for_5m(df, df_1h, eth_ta._analyze_ohlcv)
        if sc15 is None or sc1h is None:
            print(
                "Warning: TA signal filters need 15m and 1h klines; missing HTF series — no entries will pass.",
                file=sys.stderr,
            )

    lev = float(args.leverage)
    fee_bps = float(args.fee_bps)
    long_min = float(os.environ.get("TA_LONG_ENTRY_SCORE", "0.8"))
    short_max = float(os.environ.get("TA_SHORT_ENTRY_SCORE", "-0.8"))

    balance = float(args.initial)
    signals = wins = losses = 0
    pos: dict | None = None
    last_close_idx: int | None = None
    min_bars = int(args.min_bars)

    price_tp_pct = float(os.environ.get("TA_TP_PRICE_PCT", "5"))
    price_sl_pct = float(os.environ.get("TA_SL_PRICE_PCT", "3"))

    for i in range(warmup, n):
        row = df.iloc[i]
        high_p = float(row["high"])
        low_p = float(row["low"])
        close_p = float(row["close"])
        df_i = df.iloc[: i + 1]

        if pos:
            side = pos["side"]
            entry = pos["entry_price"]
            tp_price = pos["tp_price"]
            sl_price = pos["sl_price"]
            hit_tp = hit_sl = False
            exit_price = close_p
            if side == "LONG":
                if high_p >= tp_price:
                    hit_tp = True
                    exit_price = tp_price
                elif low_p <= sl_price:
                    hit_sl = True
                    exit_price = sl_price
            else:
                if low_p <= tp_price:
                    hit_tp = True
                    exit_price = tp_price
                elif high_p >= sl_price:
                    hit_sl = True
                    exit_price = sl_price

            if hit_tp or hit_sl:
                if side == "LONG":
                    profit_pct = 100.0 * (exit_price - entry) / entry if entry else 0.0
                else:
                    profit_pct = 100.0 * (entry - exit_price) / entry if entry else 0.0
                balance = _apply_balance(balance, profit_pct, lev, fee_bps)
                if hit_tp:
                    wins += 1
                else:
                    losses += 1
                pos = None
                last_close_idx = i
            continue

        if last_close_idx is not None and min_bars > 0:
            bars_since = i - last_close_idx
            if bars_since < min_bars:
                continue

        score_5m, _ = eth_ta._analyze_ohlcv(df_i)
        side = ""
        if args.mode == "open-every":
            side = "LONG" if score_5m >= 0 else "SHORT"
        else:
            want_long = score_5m >= long_min
            want_short = score_5m <= short_max
            if want_long and not want_short:
                side = "LONG"
            elif want_short and not want_long:
                side = "SHORT"

        if not side:
            continue

        if args.filters and (sc15 is None or sc1h is None or sc15[i] is None or sc1h[i] is None):
            continue

        atr_sig = eth_ta._atr_from_df(df_i)
        tp_price, sl_price, _mode = eth_ta._fixed_tp_sl_levels(
            side, close_p, price_tp_pct, price_sl_pct, lev, atr_sig
        )

        htf: dict[str, float] = {}
        if sc15 is not None and sc15[i] is not None:
            htf["15m"] = sc15[i]
        if sc1h is not None and sc1h[i] is not None:
            htf["1h"] = sc1h[i]

        snap = _build_snap(eth_ta, score_5m, df_i, htf)
        ok, _reason = eth_ta._entry_filters_pass(snap, side, df_i)
        if not ok:
            continue

        pos = {
            "side": side,
            "entry_price": close_p,
            "tp_price": tp_price,
            "sl_price": sl_price,
        }
        signals += 1

    closed = wins + losses
    open_at_end = 1 if pos else 0
    accuracy = (100.0 * wins / closed) if closed else 0.0
    return {
        "symbol": symbol,
        "days": args.days,
        "initial": float(args.initial),
        "leverage": lev,
        "fee_bps": fee_bps,
        "signals_opened": signals,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "accuracy_pct": accuracy,
        "final_balance": balance,
        "total_return_pct": 100.0 * (balance - float(args.initial)) / float(args.initial),
        "mode": args.mode,
        "filters": bool(args.filters),
        "open_positions_at_end": open_at_end,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Backtest TA signal strategy (eth_ta_telegram TA-SIM logic). "
        "TA_SIGNAL_FILTERS is on by default (15m/1h gates); use --no-filters for open-every-style entries.",
    )
    p.set_defaults(filters=True)
    p.add_argument(
        "--no-filters",
        dest="filters",
        action="store_false",
        help="Disable TA_SIGNAL_FILTERS (no 15m/1h fetch; looser entries, more trades)",
    )
    p.add_argument("--days", type=int, required=True, help="Lookback period in days")
    p.add_argument("--initial", type=float, required=True, help="Starting balance (USDT)")
    p.add_argument("--leverage", type=float, required=True, help="Leverage (e.g. 20)")
    p.add_argument("--symbol", type=str, default="", help="Binance spot symbol (default TA_SYMBOL or ETHUSDC)")
    p.add_argument(
        "--mode",
        choices=("open-every", "threshold"),
        default="open-every",
        help="open-every: LONG if 5m score>=0 else SHORT (TA_OPEN_EVERY_DIGEST). "
        "threshold: LONG if score>=TA_LONG_ENTRY_SCORE, SHORT if <=TA_SHORT_ENTRY_SCORE.",
    )
    p.add_argument("--fee-bps", type=float, default=4.0, help="Fee per side in bps (default 4)")
    p.add_argument("--min-bars", type=int, default=1, help="Min 5m bars after a close before new entry")
    p.add_argument("--tp-price-pct", type=float, default=5.0, help="TP %% on margin when not using ATR")
    p.add_argument("--sl-price-pct", type=float, default=3.0, help="SL %% on margin when not using ATR")
    p.add_argument(
        "--underlying-tp-sl",
        action="store_true",
        help="TP/SL as underlying price %% (default: margin %% like TA_TP_SL_MARGIN_PCT=1)",
    )
    p.add_argument("--use-atr", action="store_true", help="TA_TP_SL_USE_ATR=1 (ATR mults for TP/SL)")
    args = p.parse_args()
    args.margin_tp_sl = not args.underlying_tp_sl

    try:
        r = run_backtest(args)
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    print(f"Symbol: {r['symbol']} | Period: {r['days']}d | Initial: ${r['initial']:.2f} | Leverage: {r['leverage']}x")
    print(f"Mode: {r['mode']} | TA_SIGNAL_FILTERS: {'on' if r['filters'] else 'off'}")
    print(f"Signals opened (entries): {r['signals_opened']}")
    print(f"Closed trades: {r['closed_trades']} | Wins: {r['wins']} | Losses: {r['losses']}")
    print(f"Win rate (accuracy): {r['accuracy_pct']:.2f}%")
    print(f"Final balance: ${r['final_balance']:.2f} | Total return: {r['total_return_pct']:+.2f}%")
    if r.get("open_positions_at_end"):
        print("Note: 1 position was still open at period end (not counted in win/loss).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
