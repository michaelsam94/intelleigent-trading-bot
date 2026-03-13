import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler

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
    if is_scale:
        df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()  # Drop nans, possibly create gaps in index
    nonans_index = df_X_test_nonans.index

    y_test_hat_nonans = models[0].predict_proba(df_X_test_nonans.values)  # It returns pairs or probas for 0 and 1
    y_test_hat_nonans = y_test_hat_nonans[:, 1]  # Or y_test_hat.flatten()
    y_test_hat_nonans = pd.Series(data=y_test_hat_nonans, index=nonans_index)  # Attach indexes with gaps

    # Reindex so returned Series always has len(input_index); avoids length mismatch downstream
    sr_ret = y_test_hat_nonans.reindex(input_index)
    return sr_ret
