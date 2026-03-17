"""
XGBoost classifier: same interface as classifier_gb (train/predict with optional scaling and early stopping).
"""
import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler

import xgboost as xgb

def train_predict_xgb(df_X, df_y, df_X_test, model_config: dict):
    """Train model and return predictions for the test data."""
    model_pair = train_xgb(df_X, df_y, model_config)
    return predict_xgb(model_pair, df_X_test, model_config)

def train_xgb(df_X, df_y, model_config: dict):
    """Train XGBoost model; return (model, scaler)."""
    params = model_config.get("params", {})
    is_scale = params.get("is_scale", False)

    if is_scale:
        scaler = StandardScaler()
        scaler.fit(df_X)
        X_train = scaler.transform(df_X)
    else:
        scaler = None
        X_train = df_X.values

    y_train = np.ravel(df_y.values)

    train_conf = model_config.get("train", {})
    early_stopping_rounds = train_conf.get("early_stopping_rounds")
    n_estimators = train_conf.get("n_estimators", train_conf.get("num_boost_round", 500))

    valid_set = None
    if early_stopping_rounds and early_stopping_rounds > 0 and len(X_train) > 100:
        split = max(1, int(len(X_train) * 0.9))
        X_valid = X_train[split:]
        y_valid = y_train[split:]
        X_train = X_train[:split]
        y_train = y_train[:split]
        valid_set = (X_valid, y_valid)

    scale_pos_weight = train_conf.get("scale_pos_weight")
    if scale_pos_weight is None and np.any(y_train == 1):
        n_pos = int(np.sum(y_train == 1))
        n_neg = len(y_train) - n_pos
        if n_pos > 0:
            scale_pos_weight = n_neg / n_pos

    # tree_method='hist' is much faster than 'exact' on large data; n_jobs uses all cores
    xgb_params = {
        "objective": train_conf.get("objective", "binary:logistic"),
        "max_depth": train_conf.get("max_depth", 6),
        "learning_rate": train_conf.get("learning_rate", 0.02),
        "n_estimators": n_estimators,
        "reg_alpha": train_conf.get("lambda_l1", train_conf.get("reg_alpha", 0.1)),
        "reg_lambda": train_conf.get("lambda_l2", train_conf.get("reg_lambda", 0.1)),
        "subsample": train_conf.get("subsample", 1.0),
        "colsample_bytree": train_conf.get("colsample_bytree", 1.0),
        "tree_method": train_conf.get("tree_method", "hist"),
        "max_bin": train_conf.get("max_bin", 256),
        "n_jobs": train_conf.get("n_jobs", -1),
        "verbosity": 0,
        "use_label_encoder": train_conf.get("use_label_encoder", False),
        "eval_metric": train_conf.get("eval_metric", "logloss"),
    }
    if scale_pos_weight is not None:
        xgb_params["scale_pos_weight"] = scale_pos_weight
    # XGBoost 2.0+: early_stopping_rounds must be on the constructor, not fit()
    if early_stopping_rounds and early_stopping_rounds > 0:
        xgb_params["early_stopping_rounds"] = early_stopping_rounds

    fit_kw = dict(X=X_train, y=y_train)
    if valid_set is not None:
        fit_kw["eval_set"] = [valid_set]
        fit_kw["verbose"] = 100
    else:
        fit_kw["verbose"] = 100

    model = xgb.XGBClassifier(**xgb_params)
    model.fit(**fit_kw)

    return (model, scaler)

def predict_xgb(models: tuple, df_X_test, model_config: dict):
    """Predict with (model, scaler); align columns to trained feature count if needed."""
    scaler = models[1]
    is_scale = scaler is not None

    if is_scale and hasattr(scaler, "mean_") and scaler.mean_ is not None:
        n_expected = scaler.mean_.shape[0]
        if df_X_test.shape[1] != n_expected:
            if hasattr(scaler, "feature_names_in_") and scaler.feature_names_in_ is not None:
                want = list(scaler.feature_names_in_)
                if all(c in df_X_test.columns for c in want):
                    df_X_test = df_X_test[want].copy()
                else:
                    df_X_test = df_X_test.iloc[:, :n_expected].copy()
            else:
                df_X_test = df_X_test.iloc[:, :n_expected].copy()

    input_index = df_X_test.index
    if is_scale:
        df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()
    nonans_index = df_X_test_nonans.index
    y_hat = models[0].predict_proba(df_X_test_nonans.values)[:, 1]
    sr = pd.Series(data=y_hat, index=nonans_index)
    df_ret = pd.DataFrame(index=input_index)
    df_ret["y_hat"] = sr
    return df_ret["y_hat"]
