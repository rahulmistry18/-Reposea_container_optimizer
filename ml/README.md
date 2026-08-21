# ETA Deviation Model

Predicts how many **hours** a voyage will deviate from its scheduled transit
time, using an XGBoost regressor, then converts that into a **per-diem cost
impact** in USD — the number that actually drives a repositioning decision.

## Why the training data is generated, not pulled from `data/gold/`

`pipeline/state_manager.py` advances the live fleet on a deterministic clock
(`progress = stage_hours_elapsed / transit_hours`). That's the right call for
a believable live dashboard, but it means the in-flight fleet never actually
misses its own schedule — there's no ground-truth deviation to learn from in
the Gold layer as it stands today.

The port-congestion and seasonal-disruption model already exists in
`pipeline/datasources/port_intel.py` (congestion scores, lane delay factors,
typhoon season / Suez / US-coast-labour disruptions) — it's defined but not
yet wired into the ETA math. `ml/generate_training_data.py` reuses those
**exact constants** (`DISRUPTION_ZONES`, `BASE_RATES`, lane definitions) to
simulate ~4,000 historical voyages, so the deviation target is grounded in
the same domain assumptions as the rest of the codebase rather than being
invented from scratch. Each row has a `scheduled_departure` timestamp so the
model can be evaluated on a genuine time-based holdout.

**Follow-up to make this production-real:** wire `get_congestion_delay_hours()`
from `port_intel.py` into `state_manager._advance()`'s AT_SEA branch so the
live simulator itself produces deviating ETAs, then swap the training source
from `ml/generate_training_data.py` to `data/state/container_history.json`.
Flagged here rather than done silently — it's a real code change to the
lifecycle engine, not a modeling change.

## Run it

```bash
pip install -r ml/requirements-ml.txt
PYTHONPATH=$(pwd) python -m ml.generate_training_data --rows 4000
PYTHONPATH=$(pwd) python -m ml.train_eta_model
```

## Evaluation

- **Split:** last 15% of voyages by `scheduled_departure` — time-based, not
  random — so the reported error reflects forecasting forward in time.
- **Metrics:** MAE and RMSE in hours, reported against a naive
  mean-predictor baseline for context, plus average per-voyage cost-impact
  error in USD (`deviation_hours / 24 * per_diem_rate`).
- **Output:** `ml/model_metrics.json` (numbers + feature importance),
  `ml/eta_deviation_model.json` (saved booster).

Latest run on the generated dataset: **MAE 5.2h / RMSE 6.4h** vs. a
mean-predictor baseline of MAE 20.4h / RMSE 22.3h — dominated by
`seasonal_delay_factor` and trade lane, which matches the domain logic the
data was generated from. Re-run to regenerate current numbers; don't treat
the numbers in this README as a live claim.
