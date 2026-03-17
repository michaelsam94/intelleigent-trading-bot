"""
Meta-learner: takes base model predictions (e.g. high_20_05_lc, high_20_05_gb, high_20_05_xgb, low_20_05_*) and
trains a linear model to predict (high - low) as regression target. At predict time outputs trade_score_meta.
"""
import numpy as np
import pandas as pd

from sklearn.linear_model import Ridge


def train_meta(df_X: pd.DataFrame, df_y: pd.Series, model_config: dict):
    """
    df_X: 6 columns (high_lc, high_gb, high_xgb, low_lc, low_gb, low_xgb).
    df_y: target = high_20_05 - low_20_05 (in {-1,0,1}) or continuous.
    Returns (model, None) - no scaler.
    """
    X = df_X.values.astype(np.float64)
    y = np.ravel(df_y.values)
    alpha = model_config.get("train", {}).get("alpha", 1.0)
    model = Ridge(alpha=alpha)
    model.fit(X, y)
    # Store feature names for predict-time column order
    model.feature_names_in_ = list(df_X.columns)
    return (model, None)


def predict_meta(models: tuple, df_X_test: pd.DataFrame, model_config: dict):
    """df_X_test has the 6 base prediction columns. Returns series of trade_score_meta."""
    model = models[0]
    cols = getattr(model, "feature_names_in_", None) or df_X_test.columns.tolist()
    if isinstance(cols, (list, tuple)):
        missing = [c for c in cols if c not in df_X_test.columns]
        if missing:
            raise ValueError(f"Meta model needs columns {cols}. Missing: {missing}")
        df_X_test = df_X_test[list(cols)].copy()
    # Ridge does not accept NaN; fill with 0 (neutral) so realtime rows with missing base scores still get a prediction
    X = df_X_test.fillna(0.0)
    pred = model.predict(X)
    return pd.Series(pred, index=df_X_test.index)


def train_predict_meta(df_X, df_y, df_X_test, model_config: dict):
    pair = train_meta(df_X, df_y, model_config)
    return predict_meta(pair, df_X_test, model_config)
