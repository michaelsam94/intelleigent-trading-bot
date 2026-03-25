#!/usr/bin/env python3
"""
Grid-search TA-SIM parameters against historical data (reuses backtest_ta_signals cache).

Fetches Binance klines once, then sweeps TA_TP_PRICE_PCT, TA_SL_PRICE_PCT, min bars,
TA_SF_* filters, etc. Ranks by final balance (requires enough closed trades).

Past performance does not guarantee future results — use output as a starting point only.

Examples:
  python scripts/optimize_ta_backtest.py --days 30 --initial 10 --leverage 20
  python scripts/optimize_ta_backtest.py --days 14 --initial 100 --leverage 10 --quick
  python scripts/optimize_ta_backtest.py --days 30 --initial 10 --leverage 20 --full --top 10
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import backtest_ta_signals as bts  # noqa: E402

# Keys we override during the grid (restored after each run).
OPT_ENV_KEYS = [
    "TA_TP_PRICE_PCT",
    "TA_SL_PRICE_PCT",
    "TA_MIN_BARS_BETWEEN_TRADES",
    "TA_SF_LONG_MIN",
    "TA_SF_SHORT_MAX",
    "TA_SF_ADX_MIN",
]


def _snapshot(keys: list[str]) -> dict[str, str | None]:
    return {k: os.environ.get(k) for k in keys}


def _restore(snap: dict[str, str | None]) -> None:
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def grid_quick() -> dict[str, list[str]]:
    """64 combinations — fast enough for routine use."""
    return {
        "TA_TP_PRICE_PCT": ["6", "7"],
        "TA_SL_PRICE_PCT": ["2", "2.5"],
        "TA_MIN_BARS_BETWEEN_TRADES": ["2", "4"],
        "TA_SF_LONG_MIN": ["2.0", "2.5"],
        "TA_SF_SHORT_MAX": ["-2.0", "-2.5"],
        "TA_SF_ADX_MIN": ["18", "22"],
    }


def grid_full() -> dict[str, list[str]]:
    """Larger search space (~729 combos) — slower."""
    return {
        "TA_TP_PRICE_PCT": ["5", "6", "7", "8"],
        "TA_SL_PRICE_PCT": ["2", "2.5", "3"],
        "TA_MIN_BARS_BETWEEN_TRADES": ["1", "2", "4"],
        "TA_SF_LONG_MIN": ["1.5", "2.0", "2.5"],
        "TA_SF_SHORT_MAX": ["-1.5", "-2.0", "-2.5"],
        "TA_SF_ADX_MIN": ["15", "20", "25"],
    }


def _iter_grid(grid: dict[str, list[str]]):
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    for combo in itertools.product(*vals):
        yield dict(zip(keys, combo, strict=True))


def _build_base_args(ns: argparse.Namespace) -> argparse.Namespace:
    """Namespace compatible with backtest_ta_signals.load_cache / run_simulation."""
    a = argparse.Namespace()
    a.days = ns.days
    a.initial = ns.initial
    a.leverage = ns.leverage
    a.symbol = getattr(ns, "symbol", "") or ""
    a.mode = ns.mode
    a.fee_bps = ns.fee_bps
    a.min_bars = ns.min_bars  # base; grid overrides via env
    a.tp_price_pct = ns.tp_price_pct
    a.sl_price_pct = ns.sl_price_pct
    a.margin_tp_sl = not ns.underlying_tp_sl
    a.use_atr = ns.use_atr
    a.filters = ns.filters
    return a


def main() -> int:
    p = argparse.ArgumentParser(
        description="Grid-search TA-SIM env vars; ranks by final backtest balance.",
    )
    p.set_defaults(filters=True)
    p.add_argument("--no-filters", dest="filters", action="store_false")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--initial", type=float, required=True)
    p.add_argument("--leverage", type=float, required=True)
    p.add_argument("--symbol", type=str, default="")
    p.add_argument("--mode", choices=("open-every", "threshold"), default="open-every")
    p.add_argument("--fee-bps", type=float, default=4.0)
    p.add_argument(
        "--min-bars",
        type=int,
        default=1,
        help="Base min bars (grid also tries TA_MIN_BARS_BETWEEN_TRADES values)",
    )
    p.add_argument("--tp-price-pct", type=float, default=5.0)
    p.add_argument("--sl-price-pct", type=float, default=3.0)
    p.add_argument("--underlying-tp-sl", action="store_true")
    p.add_argument("--use-atr", action="store_true")
    p.add_argument("--quick", action="store_true", help="Smaller grid (default if neither --quick nor --full)")
    p.add_argument("--full", action="store_true", help="Larger grid (slower)")
    p.add_argument("--top", type=int, default=5, help="How many rows to print")
    p.add_argument(
        "--min-closed",
        type=int,
        default=15,
        help="Ignore runs with fewer closed trades (avoids degenerate 'no trade' winners)",
    )
    args = p.parse_args()
    if not args.quick and not args.full:
        args.quick = True
    grid = grid_full() if args.full else grid_quick()

    base = _build_base_args(args)
    try:
        cache = bts.load_cache(base)
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    results: list[tuple[dict[str, str], dict]] = []
    n_grid = 1
    for v in grid.values():
        n_grid *= len(v)
    print(f"Grid size: {n_grid} combinations | period {args.days}d | min-closed >= {args.min_closed}", flush=True)

    snap_start = _snapshot(OPT_ENV_KEYS)
    try:
        for i, extra in enumerate(_iter_grid(grid), start=1):
            bts.apply_cli_env(base)
            for k, v in extra.items():
                os.environ[k] = v
            r = bts.run_simulation(base, cache)

            if r["closed_trades"] < args.min_closed:
                continue
            results.append((extra, r))
            if i % 50 == 0:
                print(f"  ... {i}/{n_grid}", flush=True)
    finally:
        _restore(snap_start)

    if not results:
        print("No run met --min-closed; lower --min-closed or widen --days.", file=sys.stderr)
        return 1

    # Sort by final balance, then total return, then win rate
    results.sort(
        key=lambda x: (
            x[1]["final_balance"],
            x[1]["total_return_pct"],
            x[1]["accuracy_pct"],
        ),
        reverse=True,
    )

    top_n = min(args.top, len(results))
    print()
    print("Top combinations (copy into .env for eth_ta_telegram / TA-SIM):")
    print("-" * 72)
    best = results[0][0]
    for rank in range(top_n):
        extra, r = results[rank]
        parts = " ".join(f"{k}={extra[k]}" for k in sorted(extra.keys()))
        print(
            f"#{rank + 1}  balance=${r['final_balance']:.2f}  return={r['total_return_pct']:+.2f}%  "
            f"closed={r['closed_trades']}  win%={r['accuracy_pct']:.1f}  |  {parts}"
        )
    print("-" * 72)
    print()
    print("Suggested .env snippet (best row):")
    for k in sorted(best.keys()):
        print(f"{k}={best[k]}")
    print()
    print(
        "Note: High backtest returns often overfit past data. Re-run on an out-of-sample window "
        "before relying on live trading."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
