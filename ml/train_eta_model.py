"""
ETA DEVIATION MODEL — train_eta_model.py
===========================================
Trains an XGBoost regressor to predict `eta_deviation_hours` (how far a
voyage lands from its scheduled transit time) from lane/vessel/congestion
features, evaluated on a TIME-BASED holdout (not a random split — the last
15% of voyages by scheduled_departure are held out, so the model is judged
on its ability to generalize forward in time, the way it would actually be
used in production).

Also converts the predicted deviation into a per-diem cost-impact figure,
since that's the number that actually matters to a repositioning decision.

Run:
    PYTHONPATH=$(pwd) python -m ml.generate_training_data     # if not already run
    PYTHONPATH=$(pwd) python -m ml.train_eta_model

Outputs:
    ml/model_metrics.json          MAE / RMSE / feature importance
    ml/eta_deviation_model.json    saved XGBoost booster
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
import xgboost as xgb

DATA_PATH = Path(__file__).parent / "data" / "eta_training_data.csv"
MODEL_PATH = Path(__file__).parent / "eta_deviation_model.json"
METRICS_PATH = Path(__file__).parent / "model_metrics.json"

CATEGORICAL = ["trade_lane", "lease_type", "vessel_class"]
NUMERIC = [
    "departure_month", "departure_dow", "scheduled_transit_hours",
    "origin_congestion", "dest_congestion", "seasonal_delay_factor",
]
TARGET = "eta_deviation_hours"

HOLDOUT_FRACTION = 0.15


def load_features():
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"{DATA_PATH} not found. Run `python -m ml.generate_training_data` first."
        )
    df = pd.read_csv(DATA_PATH, parse_dates=["scheduled_departure"])
    df = df.sort_values("scheduled_departure").reset_index(drop=True)

    df_enc = pd.get_dummies(df, columns=CATEGORICAL, drop_first=False)
    feature_cols = NUMERIC + [c for c in df_enc.columns if c.startswith(tuple(f"{c}_" for c in CATEGORICAL))]
    return df, df_enc, feature_cols


def time_based_split(df_enc: pd.DataFrame, feature_cols: list[str]):
    n = len(df_enc)
    split_idx = int(n * (1 - HOLDOUT_FRACTION))

    train = df_enc.iloc[:split_idx]
    test = df_enc.iloc[split_idx:]

    X_train, y_train = train[feature_cols], train[TARGET]
    X_test, y_test = test[feature_cols], test[TARGET]
    return X_train, y_train, X_test, y_test, split_idx


def train_and_evaluate():
    df, df_enc, feature_cols = load_features()
    X_train, y_train, X_test, y_test, split_idx = time_based_split(df_enc, feature_cols)

    print(f"Train: {len(X_train)} voyages ({df['scheduled_departure'].iloc[0].date()} -> "
          f"{df['scheduled_departure'].iloc[split_idx - 1].date()})")
    print(f"Test:  {len(X_test)} voyages ({df['scheduled_departure'].iloc[split_idx].date()} -> "
          f"{df['scheduled_departure'].iloc[-1].date()})  [time-based holdout, not random]")

    model = xgb.XGBRegressor(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        random_state=42,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))

    # Naive baseline for comparison: always predict the train-set mean deviation.
    baseline_pred = np.full_like(y_test, fill_value=y_train.mean(), dtype=float)
    baseline_mae = mean_absolute_error(y_test, baseline_pred)
    baseline_rmse = float(np.sqrt(mean_squared_error(y_test, baseline_pred)))

    # ---- Cost-impact conversion --------------------------------------------
    # deviation_hours -> extra/avoided demurrage exposure at this voyage's per-diem rate
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    predicted_cost_impact = (preds / 24.0) * test_df["per_diem_rate_usd"].values
    actual_cost_impact = (test_df[TARGET].values / 24.0) * test_df["per_diem_rate_usd"].values
    cost_mae = float(mean_absolute_error(actual_cost_impact, predicted_cost_impact))

    importances = dict(zip(feature_cols, model.feature_importances_.round(4).tolist()))
    importances = dict(sorted(importances.items(), key=lambda kv: -kv[1]))

    metrics = {
        "holdout_type": "time_based (last 15% of voyages by scheduled_departure)",
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "mae_hours": round(mae, 2),
        "rmse_hours": round(rmse, 2),
        "baseline_mean_predictor_mae_hours": round(baseline_mae, 2),
        "baseline_mean_predictor_rmse_hours": round(baseline_rmse, 2),
        "cost_impact_mae_usd_per_voyage": round(cost_mae, 2),
        "feature_importance": importances,
    }

    METRICS_PATH.write_text(json.dumps(metrics, indent=2))
    model.save_model(MODEL_PATH)

    print("\n--- Results (time-based holdout) ---")
    print(f"MAE:  {mae:.2f} hours   (baseline mean-predictor MAE: {baseline_mae:.2f} hours)")
    print(f"RMSE: {rmse:.2f} hours  (baseline mean-predictor RMSE: {baseline_rmse:.2f} hours)")
    print(f"Avg per-voyage cost-impact error: ${cost_mae:,.2f}")
    print(f"\nTop features: {list(importances.items())[:5]}")
    print(f"\nSaved model -> {MODEL_PATH}")
    print(f"Saved metrics -> {METRICS_PATH}")
    return metrics


if __name__ == "__main__":
    train_and_evaluate()
