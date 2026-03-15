import warnings

import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, SGDClassifier


class _ConstantBinaryClassifier:
    """Predicts a constant 0 or 1 with predict_proba shape (n, 2) for pipeline compatibility."""

    def __init__(self, constant: int):
        self.constant = constant

    def predict_proba(self, X):
        n = X.shape[0]
        c = self.constant
        return np.column_stack([1 - c, c]).astype(np.float64)

def train_predict_lc(df_X, df_y, df_X_test, model_config: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    model_pair = train_lc(df_X, df_y, model_config)
    y_test_hat = predict_lc(model_pair, df_X_test, model_config)
    return y_test_hat

def train_lc(df_X, df_y, model_config: dict):
    """
    Train model with the specified hyper-parameters and return this model (and scaler if any).
    """
    params = model_config.get("params", {})

    is_scale = params.get("is_scale", True)
    is_regression = params.get("is_regression", False)

    #
    # Scale
    #
    if is_scale:
        scaler = StandardScaler()
        scaler.fit(df_X)
        X_train = scaler.transform(df_X)
    else:
        scaler = None
        X_train = df_X.values

    y_train = np.ravel(df_y.values)
    n_classes = len(np.unique(y_train))
    if n_classes < 2:
        constant = int(y_train[0])
        # Set "allow_constant_fallback": false in algorithm params to require real LC and fail instead
        if not params.get("allow_constant_fallback", True):
            raise ValueError(
                "Training data has only one class (all same label). "
                "Need both 0 and 1 for LogisticRegression. Use more data (train_length, download more 1m history), "
                "or lower label threshold (e.g. 1.0 for 1% move in 15 min), then re-run download → merge → features → labels → train."
            )
        print(f"WARNING: Only one class in training data (all {constant}). Using constant predictor until more data is available.")
        model = _ConstantBinaryClassifier(constant)
        return (model, scaler)

    #
    # Create model
    #
    train_conf = model_config.get("train", {})
    args = {k: v for k, v in train_conf.items() if k not in ("n_jobs", "penalty")}
    args["verbose"] = 0
    model = LogisticRegression(**args)

    #
    # Train
    #
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=ConvergenceWarning, module="sklearn")
        model.fit(X_train, y_train)

    return (model, scaler)

def predict_lc(models: tuple, df_X_test, model_config: dict):
    """
    Use the model(s) to make predictions for the test data.
    The first model is a prediction model and the second model (optional) is a scaler.
    """
    #
    # Scale
    #
    scaler = models[1]
    is_scale = scaler is not None

    input_index = df_X_test.index
    X_orig = df_X_test.copy()
    if is_scale:
        # Avoid all-NaN from scaler (e.g. scale_ was 0 when fitted on 1 row)
        if hasattr(scaler, "scale_") and scaler.scale_ is not None:
            scale_safe = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
            df_X_test = (df_X_test.values - scaler.mean_) / scale_safe
        else:
            df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()  # Drop nans, possibly create gaps in index
    nonans_index = df_X_test_nonans.index

    if len(nonans_index) == 0:
        # Scaler produced all NaN; predict on last row with safe scaling so we get at least one value
        last_row = np.asarray(X_orig.iloc[-1:].values, dtype=np.float64)
        if np.any(np.isnan(last_row)):
            if is_scale and hasattr(scaler, "mean_"):
                last_row = np.where(np.isnan(last_row), scaler.mean_, last_row)
            else:
                last_row = np.nan_to_num(last_row, nan=0.0)
        if is_scale and hasattr(scaler, "scale_") and scaler.scale_ is not None:
            scale_safe = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
            last_row = (last_row - scaler.mean_) / scale_safe
        elif is_scale:
            last_row = scaler.transform(X_orig.iloc[-1:])
        proba = models[0].predict_proba(last_row)
        y_vals = np.atleast_1d(proba[:, 1].squeeze())
        pred_index = input_index[-1:]
    else:
        proba = models[0].predict_proba(df_X_test_nonans.values)
        y_vals = np.atleast_1d(proba[:, 1].squeeze())
        n_vals = len(y_vals)
        pred_index = nonans_index[-n_vals:] if n_vals < len(nonans_index) else nonans_index

    sr_ret = pd.Series(data=y_vals, index=pred_index).reindex(input_index)
    return sr_ret
