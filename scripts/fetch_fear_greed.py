#!/usr/bin/env python3
"""
Fetch Fear & Greed Index from alternative.me and save to data/fear_greed.csv.
Merge by date into 1m data in a feature generator (fear_greed_daily).
"""
import json
from pathlib import Path

import click
import pandas as pd
import requests

from service.App import load_config, App


@click.command()
@click.option("--config_file", "-c", type=click.Path(), default="", help="Config (for data_folder)")
@click.option("--limit", default=365, help="Days to fetch")
@click.option("--out", type=click.Path(), default=None, help="Output path (default data/fear_greed.csv)")
def main(config_file, limit, out):
    if config_file:
        load_config(config_file)
        data_folder = Path(App.config.get("data_folder", "data"))
    else:
        data_folder = Path("data")
    data_folder.mkdir(parents=True, exist_ok=True)
    out_path = Path(out) if out else data_folder / "fear_greed.csv"

    url = f"https://api.alternative.me/fng/?limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"ERROR: Failed to fetch Fear & Greed: {e}")
        return

    if data.get("metadata", {}).get("error") or "data" not in data:
        print("ERROR: Unexpected API response")
        return

    rows = []
    for d in data["data"]:
        rows.append({
            "date": d["timestamp"][:10],
            "value": int(d["value"]),
            "value_classification": d.get("value_classification", ""),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").drop_duplicates("date")
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
