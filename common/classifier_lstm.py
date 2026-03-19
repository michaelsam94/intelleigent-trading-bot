"""
LSTM sequence model (Keras): takes windows of features, outputs binary probability.
Params: sequence_length (e.g. 60), is_scale, and train: units, dropout, epochs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler

_TF_IMPORT_ERROR: BaseException | None = None
try:
    import tensorflow as tf
    keras = tf.keras
    layers = tf.keras.layers
except BaseException as e:  # ImportError, OSError (missing .so), etc.
    keras = None  # type: ignore[assignment]
    layers = None  # type: ignore[assignment]
    _TF_IMPORT_ERROR = e


def _build_model(seq_len, n_features, units=32, dropout=0.2):
    model = keras.Sequential([
        layers.LSTM(units, return_sequences=False, input_shape=(seq_len, n_features)),
        layers.Dropout(dropout),
        layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def _require_tf():
    if keras is None:
        hint = (
            "TensorFlow is required for LSTM (config algorithm 'dl').\n"
            "  source venv/bin/activate && pip install -r requirements.txt\n"
            "Or: pip install 'tensorflow>=2.15'\n"
            "On small VPS, try: pip install tensorflow-cpu  (if available for your platform)\n"
        )
        if _TF_IMPORT_ERROR is not None:
            raise ImportError(hint + f"Import failed: {_TF_IMPORT_ERROR!r}") from _TF_IMPORT_ERROR
        raise ImportError(hint)


def train_lstm(df_X: pd.DataFrame, df_y: pd.Series, model_config: dict):
    _require_tf()
    params = model_config.get("params", {})
    seq_len = int(params.get("sequence_length", 60))
    is_scale = params.get("is_scale", True)

    X = df_X.values.astype(np.float32)
    y = np.ravel(df_y.values).astype(np.float32)
    n_features = X.shape[1]
    if len(X) < seq_len + 10:
        raise ValueError(f"Need at least sequence_length+10 rows (have {len(X)}, seq_len={seq_len})")

    X_seq = np.array([X[i:i + seq_len] for i in range(len(X) - seq_len + 1)])
    y_seq = y[seq_len - 1:]

    if is_scale:
        scaler = StandardScaler()
        X_flat = X_seq.reshape(-1, n_features)
        scaler.fit(X_flat)
        X_flat = scaler.transform(X_flat)
        X_seq = X_flat.reshape(X_seq.shape)
    else:
        scaler = None

    train_conf = model_config.get("train", {})
    units = train_conf.get("units", 32)
    dropout = train_conf.get("dropout", 0.2)
    epochs = train_conf.get("epochs", 20)
    batch_size = train_conf.get("batch_size", 64)
    verbose = train_conf.get("verbose", 0)

    model = _build_model(seq_len, n_features, units=units, dropout=dropout)
    model.fit(X_seq, y_seq, epochs=epochs, batch_size=batch_size, verbose=verbose)
    return (model, scaler)


def predict_lstm(models: tuple, df_X_test: pd.DataFrame, model_config: dict):
    _require_tf()
    model, scaler = models[0], models[1]
    params = model_config.get("params", {})
    seq_len = int(params.get("sequence_length", 60))

    X = df_X_test.values.astype(np.float32)
    if scaler is not None:
        X = scaler.transform(X)
    n_features = X.shape[1]
    if len(X) < seq_len:
        pred = np.full(len(X), np.nan)
        pred[-1] = model.predict(X[-seq_len:].reshape(1, seq_len, n_features), verbose=0)[0, 0]
    else:
        X_seq = np.array([X[i:i + seq_len] for i in range(len(X) - seq_len + 1)])
        p = model.predict(X_seq, verbose=0)[:, 0]
        pred = np.full(len(X), np.nan)
        pred[seq_len - 1:] = p
    return pd.Series(pred, index=df_X_test.index)


def train_predict_lstm(df_X, df_y, df_X_test, model_config: dict):
    pair = train_lstm(df_X, df_y, model_config)
    return predict_lstm(pair, df_X_test, model_config)
