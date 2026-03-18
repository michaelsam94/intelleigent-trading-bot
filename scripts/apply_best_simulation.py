#!/usr/bin/env python3
"""
Read simulation results (signal_models.txt), pick the run with the highest gain
(total_return_pct or %profit), and update the config so threshold_rule uses those parameters.
Run after: python -m scripts.simulate -c config.jsonc -d 14  (with starting_balance so total_return_pct exists).
Usage: python -m scripts.apply_best_simulation -c configs/config-1min-realtime.jsonc [--dry-run]
"""
import re
import argparse
from pathlib import Path

from service.App import load_config, App


def main():
    p = argparse.ArgumentParser(description="Apply best simulation parameters to config.")
    p.add_argument("-c", "--config", required=True, help="Config file to update")
    p.add_argument("--dry-run", action="store_true", help="Print best params only, do not write config")
    args = p.parse_args()

    load_config(args.config)
    config = App.config
    data_folder = Path(config["data_folder"])
    symbol = config["symbol"]
    out_name = config.get("signal_models_file_name", "signal_models")
    path = (data_folder / symbol / out_name).with_suffix(".txt")

    if not path.exists():
        print(f"ERROR: Simulation results not found: {path}")
        print("Run: python -m scripts.simulate -c <config> -d <days>  (e.g. with -i for balance)")
        return 1

    with open(path) as f:
        content = f.read()

    # Parse: find header and all data rows (skip empty lines; treat first non-empty as header if it contains buy_signal_threshold)
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        print("ERROR: No data in simulation results.")
        return 1

    header = lines[0].split(",")
    try:
        i_buy = header.index("buy_signal_threshold")
        i_sell = header.index("sell_signal_threshold")
    except ValueError:
        print("ERROR: Header missing buy_signal_threshold or sell_signal_threshold.")
        return 1

    i_return = header.index("total_return_pct") if "total_return_pct" in header else None
    i_profit = header.index("%profit") if "%profit" in header else None

    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < max(i_buy, i_sell) + 1:
            continue
        try:
            buy_t = float(parts[i_buy])
            sell_t = float(parts[i_sell])
        except (ValueError, TypeError):
            continue
        gain_key = None
        if i_return is not None and i_return < len(parts):
            try:
                gain_key = float(parts[i_return])
            except (ValueError, TypeError):
                pass
        if gain_key is None and i_profit is not None and i_profit < len(parts):
            try:
                gain_key = float(parts[i_profit])
            except (ValueError, TypeError):
                pass
        if gain_key is None:
            gain_key = float("-inf")
        rows.append((gain_key, buy_t, sell_t, ln))

    if not rows:
        print("ERROR: No valid data rows in simulation results.")
        return 1

    rows.sort(key=lambda x: x[0], reverse=True)
    best_gain, best_buy, best_sell, _ = rows[0]

    print(f"Best run: total_return_pct/%profit = {best_gain}")
    print(f"  buy_signal_threshold:  {best_buy}")
    print(f"  sell_signal_threshold: {best_sell}")

    if args.dry_run:
        print("Dry run: config not modified.")
        return 0

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"ERROR: Config file not found: {config_path}")
        return 1

    with open(config_path) as f:
        text = f.read()

    # Replace threshold values in threshold_rule parameters (preserve JSONC)
    # Match "buy_signal_threshold": 0.015 or 0.015,
    old_buy_pat = re.compile(r'("buy_signal_threshold"\s*:\s*)[-0-9.]+')
    old_sell_pat = re.compile(r'("sell_signal_threshold"\s*:\s*)[-0-9.]+')
    new_text = old_buy_pat.sub(rf'\g<1>{best_buy}', text, count=1)
    new_text = old_sell_pat.sub(rf'\g<1>{best_sell}', new_text, count=1)

    if new_text == text:
        print("WARNING: No threshold_rule parameters found in config; nothing updated.")
        return 0

    with open(config_path, "w") as f:
        f.write(new_text)

    print(f"Updated {config_path} with best thresholds.")
    return 0


if __name__ == "__main__":
    exit(main())
