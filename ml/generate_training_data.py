"""
ETA DEVIATION — TRAINING DATA GENERATOR
=========================================
Why this file exists
---------------------
RepoSea's live simulator (pipeline/state_manager.py) advances each container
on a *deterministic* clock: `progress = stage_hours_elapsed / transit_hours`.
That's correct for driving a believable live dashboard, but it means the
in-flight fleet never actually deviates from its own schedule — there is no
ground-truth "ETA missed by N hours" signal to learn from in `data/gold/`.

The port-congestion and seasonal-disruption model already exists in
`pipeline/datasources/port_intel.py` (congestion scores, lane delay factors,
typhoon season / Suez / US-coast-labour disruptions) — it's just never wired
into the ETA math. This script reuses those *exact* constants to simulate a
large batch of completed historical voyages, so the deviation target is
grounded in the same domain logic as the rest of the codebase instead of
being invented from scratch.

Output: ml/data/eta_training_data.csv — one row per completed voyage, with
a `scheduled_departure` column so train_eta_model.py can do a genuine
time-based (not random) holdout split.

Run:
    PYTHONPATH=$(pwd) python -m ml.generate_training_data --rows 4000
"""
import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from pipeline.datasources.port_intel import DISRUPTION_ZONES
from pipeline.datasources.market_rates import BASE_RATES

OUT_PATH = Path(__file__).parent / "data" / "eta_training_data.csv"

TRADE_LANES = ["TRANS_PAC", "TRANS_ATL", "ASIA_EUR", "INTRA_ASIA"]
LEASE_TYPES = ["One-Way", "Master Lease", "Long-Term"]

# Baseline scheduled transit hours by lane (mirrors realistic transit times;
# state_manager.py generates these per-container at creation from a similar range).
BASE_TRANSIT_HOURS = {
    "TRANS_PAC":  {"min": 240, "max": 336},   # ~10-14 days
    "TRANS_ATL":  {"min": 168, "max": 240},   # ~7-10 days
    "ASIA_EUR":   {"min": 480, "max": 648},   # ~20-27 days
    "INTRA_ASIA": {"min": 72,  "max": 168},   # ~3-7 days
}

VESSEL_SIZE_CLASS = ["Feeder", "Panamax", "Neopanamax", "ULCV"]

RNG = random.Random(42)


def _lane_seasonal_factor(lane: str, month: int) -> float:
    """Same lookup logic as port_intel.py's _synthetic_congestion()."""
    factor = 1.0
    for _, zone in DISRUPTION_ZONES.items():
        if lane in zone["affected_lanes"] and month in zone["months"]:
            factor *= zone["delay_factor"]
    return factor


def _synthetic_row(departure: datetime) -> dict:
    lane = RNG.choice(TRADE_LANES)
    lease = RNG.choice(LEASE_TYPES)
    vessel_class = RNG.choices(
        VESSEL_SIZE_CLASS, weights=[0.15, 0.30, 0.35, 0.20]
    )[0]

    base = BASE_TRANSIT_HOURS[lane]
    scheduled_hours = RNG.uniform(base["min"], base["max"])

    # Port congestion at origin + destination — same 0-1 scale as port_intel.py
    origin_congestion = max(0.0, min(1.0, RNG.gauss(0.35, 0.18)))
    dest_congestion = max(0.0, min(1.0, RNG.gauss(0.35, 0.18)))

    # Seasonal / weather disruption factor, looked up the same way the live
    # pipeline's port_intel module computes lane_delay_factor.
    seasonal_factor = _lane_seasonal_factor(lane, departure.month)

    # Bigger ships wait longer for a berth slot but are less schedule-sensitive
    # to weather once underway.
    vessel_wait_sensitivity = {
        "Feeder": 0.6, "Panamax": 0.85, "Neopanamax": 1.0, "ULCV": 1.3
    }[vessel_class]

    # Ground-truth delay model (hours) — congestion + seasonal + noise.
    congestion_delay = (origin_congestion * 14 + dest_congestion * 22) * vessel_wait_sensitivity
    seasonal_delay = (seasonal_factor - 1.0) * scheduled_hours * 0.5
    noise = RNG.gauss(0, 6)

    deviation_hours = max(-scheduled_hours * 0.15, congestion_delay + seasonal_delay + noise)

    per_diem = BASE_RATES[lease][lane]["base"]

    return {
        "container_id": f"SIM{RNG.randint(1_000_000, 9_999_999)}",
        "trade_lane": lane,
        "lease_type": lease,
        "vessel_class": vessel_class,
        "scheduled_departure": departure.isoformat(),
        "departure_month": departure.month,
        "departure_dow": departure.weekday(),
        "scheduled_transit_hours": round(scheduled_hours, 1),
        "origin_congestion": round(origin_congestion, 3),
        "dest_congestion": round(dest_congestion, 3),
        "seasonal_delay_factor": round(seasonal_factor, 3),
        "per_diem_rate_usd": per_diem,
        "eta_deviation_hours": round(deviation_hours, 2),
    }


def generate(n_rows: int, start: datetime, end: datetime) -> list[dict]:
    span_days = (end - start).days
    rows = []
    for _ in range(n_rows):
        departure = start + timedelta(days=RNG.uniform(0, span_days))
        rows.append(_synthetic_row(departure))
    rows.sort(key=lambda r: r["scheduled_departure"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=4000)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-06-30")
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    rows = generate(args.rows, start, end)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {OUT_PATH}")
    print(f"Date range: {rows[0]['scheduled_departure']} -> {rows[-1]['scheduled_departure']}")


if __name__ == "__main__":
    main()
