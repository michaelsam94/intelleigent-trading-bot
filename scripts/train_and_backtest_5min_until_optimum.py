#!/usr/bin/env python3
"""
Iterative train + backtest for 5min configs: run pipeline (train) then backtest with --apply-best
until best total return reaches target_profit_pct or max_iterations is reached.

Usage (from project root):
  python -m scripts.train_and_backtest_5min_until_optimum [--max-iter 3] [--target 0] [--days 14]
  python -m scripts.train_and_backtest_5min_until_optimum --max-iter 5 --target 5 --days 14

- --max-iter: max train+backtest rounds (default 3)
- --target: stop when best total_return_pct >= this (default 0)
- --days: backtest last N days (default 14)

Note: Retraining on the same data yields the same model; profit only changes if data or
thresholds change. Use multiple iterations to re-apply best thresholds and/or re-download data.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAMES = [
    "configs/config-5min-realtime.jsonc",
    "configs/config-5min-realtime-ethusdc.jsonc",
]
CONFIGS = [PROJECT_ROOT / n for n in CONFIG_NAMES]


def load_jsonc(path: Path) -> dict:
    with open(path) as f:
        text = f.read()
    text = re.sub(r"//.*", "", text)
    import json
    return json.loads(text)


def set_train(path: Path, train: bool) -> None:
    with open(path) as f:
        text = f.read()
    if train:
        text = re.sub(r'"train"\s*:\s*false', '"train": true', text, count=1, flags=re.I)
    else:
        text = re.sub(r'"train"\s*:\s*true', '"train": false', text, count=1, flags=re.I)
    with open(path, "w") as f:
        f.write(text)


def get_best_return_from_signal_models(data_folder: str, symbol: str) -> float | None:
    path = Path(data_folder).resolve() / symbol / "signal_models.txt"
    if not path.exists():
        return None
    with open(path) as f:
        content = f.read()
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return None
    header = lines[0].split(",")
    if "total_return_pct" not in header:
        return None
    i_return = header.index("total_return_pct")
    best = None
    for ln in lines[1:]:
        parts = ln.split(",")
        if i_return >= len(parts):
            continue
        try:
            val = float(parts[i_return])
            if best is None or val > best:
                best = val
        except (ValueError, TypeError):
            continue
    return best


def main() -> int:
    p = argparse.ArgumentParser(description="Train + backtest 5min configs until target profit or max iterations.")
    p.add_argument("--max-iter", type=int, default=3, help="Max train+backtest rounds (default 3)")
    p.add_argument("--target", type=float, default=0.0, help="Stop when best total_return_pct >= this (default 0)")
    p.add_argument("--days", type=int, default=14, help="Backtest last N days (default 14)")
    args = p.parse_args()

    for cfg in CONFIGS:
        if not cfg.is_file():
            print(f"ERROR: Config not found: {cfg}")
            return 1

    # Ensure train is true for pipeline
    for cfg in CONFIGS:
        set_train(cfg, True)

    try:
        for iteration in range(1, args.max_iter + 1):
            print(f"\n{'='*60}")
            print(f"  ITERATION {iteration}/{args.max_iter}")
            print("="*60)

            # 1) Clear previous backtest results so this run's backtest is the only block in signal_models.txt
            for cfg in CONFIGS:
                data = load_jsonc(cfg)
                data_folder = data.get("data_folder", "./data")
                for sym in ("BTCUSDC", "ETHUSDC"):
                    p = (PROJECT_ROOT / data_folder / sym / "signal_models.txt").resolve()
                    if p.exists():
                        p.unlink()

            # 2) Pipeline (download, merge, features, labels, train, predict, signals)
            print("\n--- Pipeline (train) ---")
            r = subprocess.run(
                ["bash", str(PROJECT_ROOT / "scripts" / "run_pipeline_to_signals.sh")] + CONFIG_NAMES,
                cwd=PROJECT_ROOT,
                shell=False,
            )
            if r.returncode != 0:
                print("Pipeline failed. Stopping.")
                return 1

            # 3) Backtest with apply-best
            print("\n--- Backtest (apply best thresholds) ---")
            r = subprocess.run(
                ["bash", str(PROJECT_ROOT / "scripts" / "run_backtest_5min.sh"), str(args.days), "--apply-best"],
                cwd=PROJECT_ROOT,
                shell=False,
            )
            if r.returncode != 0:
                print("Backtest failed. Stopping.")
                return 1

            # 4) Read best total_return_pct for each symbol
            data_dir = str(PROJECT_ROOT / "data")
            best_btc = get_best_return_from_signal_models(data_dir, "BTCUSDC")
            best_eth = get_best_return_from_signal_models(data_dir, "ETHUSDC")
            print(f"\n  Best total_return_pct: BTCUSDC={best_btc}%  ETHUSDC={best_eth}%")

            # Use the worse of the two (or min) so we require both to be above target
            best_overall = None
            if best_btc is not None and best_eth is not None:
                best_overall = min(best_btc, best_eth)
            elif best_btc is not None:
                best_overall = best_btc
            elif best_eth is not None:
                best_overall = best_eth

            if best_overall is not None and best_overall >= args.target:
                print(f"\n  Target reached: best {best_overall}% >= {args.target}%. Stopping.")
                break
            if iteration == args.max_iter:
                print(f"\n  Max iterations ({args.max_iter}) reached. Stopping.")
    finally:
        # Restore train false for server
        for cfg in CONFIGS:
            set_train(cfg, False)
        print("\n  Set train: false in both configs for server.")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
