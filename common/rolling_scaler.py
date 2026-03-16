"""
Rolling Z-score normalisation: (x - rolling_mean) / rolling_std.
Use for time-series to avoid global scaling; set in algorithm params: use_rolling_zscore: true, rolling_window: 100.
"""
import numpy as np
import pandas as pd


class RollingZScoreScaler:
    """Scale features using rolling mean and std (z-score over lookback window)."""

    def __init__(self, window=100, min_periods=None):
        self.window = int(window)
        self.min_periods = min_periods if min_periods is not None else max(1, self.window // 2)
        self.feature_names_in_ = None
        self.n_features_in_ = None

    def fit(self, X, y=None):
        if isinstance(X, pd.DataFrame):
            self.feature_names_in_ = X.columns.tolist()
            self.n_features_in_ = X.shape[1]
        else:
            self.n_features_in_ = X.shape[1] if hasattr(X, "shape") else None
        return self

    def transform(self, X):
        if isinstance(X, pd.DataFrame):
            df = X
            vals = df.values
        else:
            vals = np.asarray(X)
            df = pd.DataFrame(vals)
        rmean = df.rolling(self.window, min_periods=self.min_periods).mean()
        rstd = df.rolling(self.window, min_periods=self.min_periods).std()
        rstd = rstd.replace(0, np.nan).fillna(1.0)
        out = (vals - rmean.values) / rstd.values
        return out

    @property
    def mean_(self):
        return np.zeros(self.n_features_in_) if self.n_features_in_ else None

    @property
    def scale_(self):
        return np.ones(self.n_features_in_) if self.n_features_in_ else None


def rolling_zscore_transform(df_X, window, min_periods=None):
    """Apply rolling z-score to a DataFrame; returns numpy array."""
    min_periods = min_periods or max(1, window // 2)
    rmean = df_X.rolling(window, min_periods=min_periods).mean()
    rstd = df_X.rolling(window, min_periods=min_periods).std()
    rstd = rstd.replace(0, np.nan).fillna(1.0)
    return ((df_X - rmean) / rstd).values
