#!/usr/bin/env python3
"""
Bayesian hyperparameter search with Optuna for GB/XGB (and optionally LC).
Loads matrix, runs cross-validation or walk-forward, optimizes params, writes best to JSON.

Usage:
  python -m scripts.optuna_tune -c configs/config-1min-realtime.jsonc --algo gb --n_trials 20
  python -m scripts.optuna_tune -c configs/config-1min-realtime.jsonc --algo xgb --n_trials 20 --out best_xgb.json
"""
import json
import re
from pathlib import Path

import click
import numpy as np
import pandas as pd

from service.App import load_config, App

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    optuna = None


@click.command()
@click.option("--config_file", "-c", type=click.Path(), required=True)
@click.option("--algo", type=click.Choice(["gb", "xgb"]), default="gb")
@click.option("--n_trials", type=int, default=30)
@click.option("--out", type=click.Path(), default=None)
@click.option("--label", default="high_20_05")
def main(config_file, algo, n_trials, out, label):
    load_config(config_file)
    config = App.config

    if optuna is None:
        print("ERROR: pip install optuna")
        return

    data_path = Path(config["data_folder"]) / config["symbol"]
    matrix_path = data_path / config.get("matrix_file_name", "matrix.csv")
    if not matrix_path.is_file():
        print(f"ERROR: {matrix_path} not found. Run pipeline through labels.")
        return

    train_features = config.get("train_features", [])
    if label not in config.get("labels", []):
        print(f"ERROR: label {label} not in config labels")
        return

    df = pd.read_csv(matrix_path, parse_dates=[config.get("time_column", "open_time")], date_format="ISO8601")
    df = df.dropna(subset=train_features + [label])
    if config.get("train_length"):
        df = df.tail(config["train_length"])
    df_X = df[train_features]
    df_y = df[label]
    if np.issubdtype(df_y.dtype, bool):
        df_y = df_y.astype(int)

    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score

    def objective(trial):
        if algo == "gb":
            import lightgbm as lgbm
            params = {
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                "num_boost_round": trial.suggest_int("num_boost_round", 200, 800),
                "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10, log=True),
                "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10, log=True),
            }
            scaler = StandardScaler()
            X = scaler.fit_transform(df_X)
            y = df_y.values
            train_set = lgbm.Dataset(X, y)
            model = lgbm.train(
                {"objective": "binary", "verbosity": -1, **params},
                train_set,
                num_boost_round=params["num_boost_round"],
            )
            pred = model.predict(X)
            pred_bin = (pred >= 0.5).astype(int)
            score = (pred_bin == y).mean()
        else:
            import xgboost as xgb
            params = {
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                "n_estimators": trial.suggest_int("n_estimators", 200, 800),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10, log=True),
            }
            scaler = StandardScaler()
            X = scaler.fit_transform(df_X)
            y = df_y.values
            model = xgb.XGBClassifier(use_label_encoder=False, eval_metric="logloss", verbosity=0, **params)
            model.fit(X, y)
            score = model.score(X, y)
        return score

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
    best = study.best_params
    print(f"Best {algo} params: {best}")

    if out:
        out_path = Path(out)
        out_path.write_text(json.dumps({algo: best}, indent=2))
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
