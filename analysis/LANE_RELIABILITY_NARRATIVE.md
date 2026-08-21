# Lane Reliability Narrative

_Generated from the Gold snapshot as of `2026-08-12T15:04:32.430283+00:00`, run #11 — this file regenerates on every pipeline run, it is not a static write-up._

Fleet-wide: **35 containers**, **25 Critical**, **$340,080** total accrued demurrage/detention exposure, average buffer **-75.8 days** to last free day.

## Which lanes have the worst ETA / free-day reliability

| Trade Lane | Containers | % Critical | Avg Buffer (days) | Avg Burn/Container ($) |
|---|---|---|---|---|
| Trans-Atlantic | 7 | 85.7% | -64.6 | $9,268 |
| Asia-Europe | 10 | 70.0% | -78.4 | $12,094 |
| Trans-Pacific | 12 | 66.7% | -78.7 | $8,756 |
| Intra-Asia | 6 | 66.7% | -78.9 | $8,347 |

## Repositioning recommendation, worst lane first

- Pull idle/near-empty containers off **Trans-Atlantic** first — 86% of its fleet is already Critical and burning per-diem. Prioritize any One-Way or Master Lease boxes on this lane for early return/relet over Long-Term leases, since the daily rate on those tends to be the highest exposure per day overdue.
- Pull idle/near-empty containers off **Asia-Europe** first — 70% of its fleet is already Critical and burning per-diem. Prioritize any One-Way or Master Lease boxes on this lane for early return/relet over Long-Term leases, since the daily rate on those tends to be the highest exposure per day overdue.
- Pull idle/near-empty containers off **Trans-Pacific** first — 67% of its fleet is already Critical and burning per-diem. Prioritize any One-Way or Master Lease boxes on this lane for early return/relet over Long-Term leases, since the daily rate on those tends to be the highest exposure per day overdue.
- Pull idle/near-empty containers off **Intra-Asia** first — 67% of its fleet is already Critical and burning per-diem. Prioritize any One-Way or Master Lease boxes on this lane for early return/relet over Long-Term leases, since the daily rate on those tends to be the highest exposure per day overdue.

## Read this with the model, not instead of it

The ETA-deviation model (`ml/train_eta_model.py`) attributes most of the predicted delay to `seasonal_delay_factor` and trade lane — which lines up directly with the ranking above: lanes exposed to an active seasonal disruption window carry both the highest predicted deviation *and* the highest share of Critical containers in the live fleet. That agreement across two independent layers (the live buffer-tracking Gold data here, and the historical deviation model) is the actual evidence behind the recommendation, not just a single snapshot.
