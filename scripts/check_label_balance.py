"""
Check label balance before training. Run after labels step, before train.
Healthy: each label 20-55% True. Skewed (e.g. one label 68%, another 18%) = training window bias; do not train.
Exit 0 if all in range, 1 otherwise so pipeline can gate.
"""
import sys
from pathlib import Path

import pandas as pd

from service.App import load_config, App

LABEL_COLS = ["high_20_03", "high_20_05", "low_20_03", "low_20_05"]
MIN_PCT = 20.0
MAX_PCT = 55.0


def main(config_file: str):
    load_config(config_file)
    config = App.config
    data_folder = Path(config["data_folder"])
    symbol = config["symbol"]
    matrix_file_name = config.get("matrix_file_name", "matrix.csv")
    labels = config.get("labels", LABEL_COLS)

    path = data_folder / symbol / matrix_file_name
    if not path.exists():
        print(f"ERROR: Matrix file not found: {path}")
        print("Run download → merge --train → features → labels first.")
        sys.exit(2)

    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    missing = [c for c in labels if c not in df.columns]
    if missing:
        print(f"ERROR: Label columns not found: {missing}")
        sys.exit(2)

    print("Label balance (% True). Healthy: 20-55%. Skewed = do not train.")
    ok = True
    for col in labels:
        pct = df[col].mean() * 100
        in_range = MIN_PCT <= pct <= MAX_PCT
        if not in_range:
            ok = False
        status = "OK" if in_range else "SKEWED"
        print(f"  {col}: {pct:.1f}% True  [{status}]")

    if not ok:
        print("\nAt least one label is outside 20-55%. Fix data window or thresholds before training.")
        sys.exit(1)
    print("\nBalance OK. Proceed with train.")
    sys.exit(0)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Check label balance after labels, before train.")
    p.add_argument("-c", "--config", required=True, help="Config file (e.g. configs/config-1min-realtime.jsonc)")
    args = p.parse_args()
    main(args.config)
