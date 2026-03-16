#!/usr/bin/env python3
"""
SHAP feature importance and pruning.
Loads a trained tree model (GB or XGB), computes SHAP values, ranks features,
and optionally writes a pruned train_features list for use in config.

Usage:
  python -m scripts.shap_feature_importance -c configs/config-1min-realtime.jsonc
  python -m scripts.shap_feature_importance -c configs/config-1min-realtime.jsonc --model high_20_05_gb --top 25 --out train_features_pruned.json
"""
import json
import re
from pathlib import Path

import click
import numpy as np
import pandas as pd
from joblib import load

from service.App import load_config, App

try:
    import shap
except ImportError:
    shap = None


@click.command()
@click.option("--config_file", "-c", type=click.Path(), required=True, help="Config file")
@click.option("--model", default="high_20_05_gb", help="Model key (e.g. high_20_05_gb or high_20_05_xgb)")
@click.option("--top", type=int, default=None, help="Keep only top N features; default all")
@click.option("--min_importance", type=float, default=None, help="Drop features with mean |SHAP| below this")
@click.option("--out", type=click.Path(), default=None, help="Write pruned feature list to JSON file")
@click.option("--sample", type=int, default=2000, help="Max rows to use for SHAP (faster)")
def main(config_file, model, top, min_importance, out, sample):
    load_config(config_file)
    config = App.config

    if shap is None:
        print("ERROR: Install shap: pip install shap")
        return

    data_path = Path(config["data_folder"]) / config["symbol"]
    matrix_path = data_path / config.get("matrix_file_name", "matrix.csv")
    if not matrix_path.is_file():
        print(f"ERROR: Matrix not found: {matrix_path}. Run pipeline through labels first.")
        return

    model_path = Path(config.get("model_path", "models")) / config["symbol"]
    scaler_path = model_path / f"{model}.scaler"
    model_file = model_path / f"{model}.pickle"
    if not model_file.is_file():
        print(f"ERROR: Model not found: {model_file}. Train first.")
        return

    train_features = config.get("train_features", [])
    labels = config.get("labels", [])
    df = pd.read_csv(matrix_path, parse_dates=[config.get("time_column", "open_time")], date_format="ISO8601")
    df = df.dropna(subset=train_features + labels)
    if config.get("train_length"):
        df = df.tail(config["train_length"])
    df_X = df[train_features]
    df_y = df[labels[0]] if labels else None

    if len(df_X) > sample:
        df_X = df_X.sample(n=sample, random_state=42)
        if df_y is not None:
            df_y = df_y.loc[df_X.index]

    scaler = load(scaler_path)
    booster = load(model_file)

    if hasattr(scaler, "transform"):
        X = scaler.transform(df_X)
        feature_names = getattr(scaler, "feature_names_in_", df_X.columns.tolist())
    else:
        X = df_X.values
        feature_names = df_X.columns.tolist()

    try:
        explainer = shap.TreeExplainer(booster)
        shap_values = explainer.shap_values(X)
    except Exception as e:
        print(f"TreeExplainer failed ({e}). Trying Explainer with masker...")
        explainer = shap.Explainer(booster, X, feature_names=feature_names)
        shap_values = explainer.shap_values(X)

    if isinstance(shap_values, list):
        shap_values = shap_values[1] if len(shap_values) > 1 else shap_values[0]
    mean_abs = np.abs(shap_values).mean(axis=0)
    order = np.argsort(mean_abs)[::-1]

    print(f"\nSHAP feature importance (model={model}, n_samples={len(X)})\n")
    print(f"{'rank':<6} {'feature':<40} {'mean_abs_shap':<16}")
    print("-" * 64)
    for r, i in enumerate(order, 1):
        name = feature_names[i] if i < len(feature_names) else f"col_{i}"
        print(f"{r:<6} {name:<40} {mean_abs[i]:.6f}")

    keep = list(order)
    if top is not None:
        keep = keep[:top]
    if min_importance is not None:
        keep = [i for i in keep if mean_abs[i] >= min_importance]
    pruned = [feature_names[i] for i in keep if i < len(feature_names)]

    if out:
        out_path = Path(out)
        out_path.write_text(json.dumps({"train_features": pruned}, indent=2))
        print(f"\nWrote {len(pruned)} features to {out_path}")

    return


if __name__ == "__main__":
    main()
