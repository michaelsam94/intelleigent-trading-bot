#!/usr/bin/env python3
"""
Backtest the TA signal strategy used by TA-SIM (scripts/eth_ta_telegram.py).

Uses Binance spot klines, the same _analyze_ohlcv score, TP/SL (_fixed_tp_sl_levels),
fees and leverage as the live paper trader.

Examples:
  python scripts/backtest_ta_signals.py
  python scripts/backtest_ta_signals.py --preset conservative
  python scripts/backtest_ta_signals.py --days 30 --initial 100 --leverage 5
  python scripts/backtest_ta_signals.py --days 14 --initial 10 --mode threshold --no-filters
  python scripts/optimize_ta_backtest.py --days 30 --initial 10 --leverage 20
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _load_project_dotenv() -> None:
    """Merge project root .env into os.environ (same precedence as live script)."""
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


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


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


def _fee_margin_pct_roundtrip(fee_bps: float, lev: float) -> float:
    """Round-trip fee as % of margin (same as eth_ta_telegram TA-SIM)."""
    return 2.0 * (fee_bps / 10000.0) * lev * 100.0


def _apply_balance(prev: float, profit_pct: float, lev: float, fee_bps: float) -> float:
    leveraged_pnl_pct = profit_pct * lev
    fee_margin_pct = _fee_margin_pct_roundtrip(fee_bps, lev)
    nxt = prev * (1.0 + leveraged_pnl_pct / 100.0 - fee_margin_pct / 100.0)
    return max(0.01, nxt)


def _max_drawdown_pct(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for b in equity:
        if b > peak:
            peak = b
        if peak > 0:
            dd = 100.0 * (peak - b) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


@dataclass
class BacktestCache:
    """Pre-fetched OHLCV + HTF scores so grid search does not re-hit Binance."""

    eth_ta: Any
    df: pd.DataFrame
    sc15: list[float | None] | None
    sc1h: list[float | None] | None
    symbol: str


def apply_cli_env(args: argparse.Namespace) -> None:
    """Set TA_* env from CLI (same as a single backtest run)."""
    if getattr(args, "symbol", None):
        os.environ["TA_SYMBOL"] = str(args.symbol).upper()
    os.environ["TA_LEVERAGE"] = str(args.leverage)
    os.environ["TA_FEE_BPS_PER_SIDE"] = str(args.fee_bps)
    os.environ["TA_TP_PRICE_PCT"] = str(args.tp_price_pct)
    os.environ["TA_SL_PRICE_PCT"] = str(args.sl_price_pct)
    if getattr(args, "sf_long_min", None) is not None:
        os.environ["TA_SF_LONG_MIN"] = str(args.sf_long_min)
    if getattr(args, "sf_short_max", None) is not None:
        os.environ["TA_SF_SHORT_MAX"] = str(args.sf_short_max)
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


def load_cache(args: argparse.Namespace) -> BacktestCache:
    """Load klines once; used by single run and optimizer."""
    eth_ta = _load_eth_ta()
    eth_ta._load_project_dotenv()
    apply_cli_env(args)

    symbol = os.environ.get("TA_SYMBOL", "ETHUSDC").strip().upper()
    client = eth_ta._client()
    end_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start_ms = end_ms - int(args.days * 86400 * 1000)

    raw = _fetch_klines_range(client, symbol, "5m", start_ms, end_ms)
    if len(raw) < 80:
        raise ValueError(f"Not enough 5m klines ({len(raw)}). Check symbol and days.")

    df = eth_ta._klines_to_df(raw)
    df = _ensure_close_time(df)

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

    return BacktestCache(eth_ta=eth_ta, df=df, sc15=sc15, sc1h=sc1h, symbol=symbol)


def run_simulation(args: argparse.Namespace, cache: BacktestCache) -> dict:
    """
    Run bar loop using preloaded cache. Reads TA_* from os.environ (set apply_cli_env + overrides first).
    """
    eth_ta = cache.eth_ta
    df = cache.df
    sc15, sc1h = cache.sc15, cache.sc1h
    symbol = cache.symbol
    n = len(df)
    warmup = 60

    lev = float(args.leverage)
    fee_bps = float(args.fee_bps)
    long_min = float(os.environ.get("TA_LONG_ENTRY_SCORE", "0.8"))
    short_max = float(os.environ.get("TA_SHORT_ENTRY_SCORE", "-0.8"))

    balance = float(args.initial)
    balance_no_fees = float(args.initial)
    equity_curve: list[float] = [balance]
    signals = wins = losses = 0
    pos: dict | None = None
    last_close_idx: int | None = None
    min_bars = int(os.environ.get("TA_MIN_BARS_BETWEEN_TRADES", str(args.min_bars)))

    price_tp_pct = float(os.environ.get("TA_TP_PRICE_PCT", "5"))
    price_sl_pct = float(os.environ.get("TA_SL_PRICE_PCT", "3"))

    gross_win_margin_net = 0.0
    gross_loss_margin_net = 0.0
    fee_per_trade = _fee_margin_pct_roundtrip(fee_bps, lev)

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
                lev_pnl = profit_pct * lev
                net_margin_pct = lev_pnl - fee_per_trade
                if net_margin_pct >= 0:
                    gross_win_margin_net += net_margin_pct
                else:
                    gross_loss_margin_net += -net_margin_pct
                balance = _apply_balance(balance, profit_pct, lev, fee_bps)
                balance_no_fees = _apply_balance(balance_no_fees, profit_pct, lev, 0.0)
                equity_curve.append(balance)
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
        min_abs = float(getattr(args, "min_abs_score", 0.0) or 0.0)
        if args.mode == "open-every":
            if min_abs > 0:
                if score_5m >= min_abs:
                    side = "LONG"
                elif score_5m <= -min_abs:
                    side = "SHORT"
                else:
                    continue
            else:
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
    if closed == 0:
        profit_factor = 0.0
    elif gross_loss_margin_net > 1e-9:
        profit_factor = gross_win_margin_net / gross_loss_margin_net
    elif gross_win_margin_net > 1e-9:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    max_dd_pct = _max_drawdown_pct(equity_curve)
    approx_fee_drag_pct = fee_per_trade * closed if closed else 0.0
    trades_per_day = closed / float(args.days) if args.days else 0.0
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
        "final_balance_no_fees": balance_no_fees,
        "total_return_pct": 100.0 * (balance - float(args.initial)) / float(args.initial),
        "total_return_pct_no_fees": 100.0 * (balance_no_fees - float(args.initial)) / float(args.initial),
        "mode": args.mode,
        "filters": bool(args.filters),
        "open_positions_at_end": open_at_end,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd_pct,
        "fee_drag_pct_approx": approx_fee_drag_pct,
        "trades_per_day": trades_per_day,
        "min_abs_score": float(getattr(args, "min_abs_score", 0.0) or 0.0),
    }


def run_backtest(args: argparse.Namespace) -> dict:
    """Single run: load data then simulate."""
    cache = load_cache(args)
    return run_simulation(args, cache)


def main() -> int:
    _load_project_dotenv()
    filters_default = _env_bool("TA_SIGNAL_FILTERS", True)
    open_every_default = _env_bool("TA_OPEN_EVERY_DIGEST", True)
    preset_default = (os.environ.get("TA_PRESET") or "none").strip().lower()
    if preset_default not in ("none", "conservative", "high-win-rate"):
        preset_default = "none"
    p = argparse.ArgumentParser(
        description="Backtest TA signal strategy (eth_ta_telegram TA-SIM logic). "
        "TA_SIGNAL_FILTERS is on by default (15m/1h gates); use --no-filters for open-every-style entries.",
    )
    p.set_defaults(filters=filters_default)
    p.add_argument(
        "--filters",
        action="store_true",
        dest="legacy_filters_alias",
        help="No-op: TA_SIGNAL_FILTERS is already on by default (kept for old scripts / docs)",
    )
    p.add_argument(
        "--no-filters",
        dest="filters",
        action="store_false",
        help="Disable TA_SIGNAL_FILTERS (no 15m/1h fetch; looser entries, more trades)",
    )
    p.add_argument(
        "--preset",
        choices=("none", "conservative", "high-win-rate"),
        default=preset_default,
        help="conservative: 2× lev, 48 bars, ATR TP/SL. high-win-rate: tight TP / wide SL margin + "
        "strong |score| gate (targets ~60%%+ win rate; expectancy may still need tuning)",
    )
    p.add_argument("--days", type=int, default=30, help="Lookback period in days (default 30)")
    p.add_argument(
        "--initial",
        type=float,
        default=float(os.environ.get("TA_STARTING_BALANCE", "10")),
        help="Starting balance USDT (default TA_STARTING_BALANCE or 10)",
    )
    p.add_argument(
        "--leverage",
        type=float,
        default=float(os.environ.get("TA_LEVERAGE", "5")),
        help="Leverage (default TA_LEVERAGE or 5; high leverage + fees often dominates backtests)",
    )
    p.add_argument(
        "--min-bars",
        type=int,
        default=int(os.environ.get("TA_MIN_BARS_BETWEEN_TRADES", "12")),
        help="Min 5m bars after a close before new entry (default TA_MIN_BARS_BETWEEN_TRADES or 12)",
    )
    p.add_argument(
        "--symbol",
        type=str,
        default=os.environ.get("TA_SYMBOL", "").strip().upper(),
        help="Binance spot symbol (default TA_SYMBOL or ETHUSDC)",
    )
    p.add_argument(
        "--mode",
        choices=("open-every", "threshold"),
        default="open-every" if open_every_default else "threshold",
        help="open-every: LONG if 5m score>=0 else SHORT (TA_OPEN_EVERY_DIGEST). "
        "threshold: LONG if score>=TA_LONG_ENTRY_SCORE, SHORT if <=TA_SHORT_ENTRY_SCORE.",
    )
    p.add_argument(
        "--fee-bps",
        type=float,
        default=float(os.environ.get("TA_FEE_BPS_PER_SIDE", "4")),
        help="Fee per side in bps (default TA_FEE_BPS_PER_SIDE or 4)",
    )
    p.add_argument(
        "--tp-price-pct",
        type=float,
        default=float(os.environ.get("TA_TP_PRICE_PCT", "6")),
        help="TP %% on margin when not using ATR (default TA_TP_PRICE_PCT or 6)",
    )
    p.add_argument(
        "--sl-price-pct",
        type=float,
        default=float(os.environ.get("TA_SL_PRICE_PCT", "2.5")),
        help="SL %% on margin when not using ATR (default TA_SL_PRICE_PCT or 2.5)",
    )
    p.add_argument(
        "--underlying-tp-sl",
        action="store_true",
        help="TP/SL as underlying price %% (default: margin %% like TA_TP_SL_MARGIN_PCT=1)",
    )
    p.add_argument(
        "--use-atr",
        action="store_true",
        default=_env_bool("TA_TP_SL_USE_ATR", False),
        help="TA_TP_SL_USE_ATR=1 (ATR mults for TP/SL)",
    )
    p.add_argument(
        "--min-abs-score",
        type=float,
        default=float(os.environ.get("TA_OPEN_EVERY_MIN_ABS_SCORE", "0")),
        help="open-every only: only enter if 5m score >= this (LONG) or <= -this (SHORT). "
        "e.g. 2.5 skips weak direction; improves win rate with fewer trades.",
    )
    p.add_argument(
        "--sf-long-min",
        type=float,
        default=float(os.environ["TA_SF_LONG_MIN"]) if "TA_SF_LONG_MIN" in os.environ else None,
        metavar="N",
        help="When filters on: override TA_SF_LONG_MIN (stricter LONGs)",
    )
    p.add_argument(
        "--sf-short-max",
        type=float,
        default=float(os.environ["TA_SF_SHORT_MAX"]) if "TA_SF_SHORT_MAX" in os.environ else None,
        metavar="N",
        help="When filters on: override TA_SF_SHORT_MAX (stricter SHORTs)",
    )
    args = p.parse_args()
    args.margin_tp_sl = not args.underlying_tp_sl
    if "TA_TP_SL_MARGIN_PCT" in os.environ and "--underlying-tp-sl" not in sys.argv[1:]:
        args.margin_tp_sl = _env_bool("TA_TP_SL_MARGIN_PCT", True)

    argv = sys.argv[1:]
    if args.preset == "conservative":
        if "--leverage" not in argv:
            args.leverage = 2.0
        if "--min-bars" not in argv:
            args.min_bars = 48
        if "--use-atr" not in argv:
            args.use_atr = True
    elif args.preset == "high-win-rate":
        # Tight TP / wide SL on margin → many TP hits; strong score + stricter filters.
        if "--leverage" not in argv:
            args.leverage = 3.0
        if "--min-bars" not in argv:
            args.min_bars = 24
        if "--tp-price-pct" not in argv:
            args.tp_price_pct = 1.2
        if "--sl-price-pct" not in argv:
            args.sl_price_pct = 10.0
        if "--min-abs-score" not in argv:
            args.min_abs_score = 2.2
        if "--use-atr" not in argv:
            args.use_atr = False
        if "--sf-long-min" not in argv:
            args.sf_long_min = 2.5
        if "--sf-short-max" not in argv:
            args.sf_short_max = -2.5

    try:
        r = run_backtest(args)
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    print(f"Symbol: {r['symbol']} | Period: {r['days']}d | Initial: ${r['initial']:.2f} | Leverage: {r['leverage']}x")
    mas = r.get("min_abs_score") or 0.0
    if mas > 0:
        print(f"Min |score| gate: {mas:.2f} (open-every)")
    print(f"Mode: {r['mode']} | TA_SIGNAL_FILTERS: {'on' if r['filters'] else 'off'}")
    print(f"Signals opened (entries): {r['signals_opened']}")
    print(f"Closed trades: {r['closed_trades']} | Wins: {r['wins']} | Losses: {r['losses']}")
    print(f"Win rate (accuracy): {r['accuracy_pct']:.2f}%")
    pf = r.get("profit_factor", 0.0)
    pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"Profit factor (net margin): {pf_s} | Max drawdown: {r.get('max_drawdown_pct', 0):.1f}%")
    print(f"Approx. fee drag (sum of round-trip margin %%): {r.get('fee_drag_pct_approx', 0):.1f}% | Trades/day: {r.get('trades_per_day', 0):.2f}")
    print(
        f"Final balance (no fees): ${r['final_balance_no_fees']:.2f} | "
        f"Total return: {r['total_return_pct_no_fees']:+.2f}%"
    )
    print(
        f"Final balance (with fees): ${r['final_balance']:.2f} | "
        f"Total return: {r['total_return_pct']:+.2f}%"
    )
    if r.get("open_positions_at_end"):
        print("Note: 1 position was still open at period end (not counted in win/loss).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
