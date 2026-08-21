# RepoSea

Real-time container fleet intelligence — tracking every voyage and flagging demurrage/detention risk before it becomes a penalty.

**Live Dashboard:** https://rahulmistry18.github.io/Reposea_container_optimizer/
**Repository:** https://github.com/rahulmistry18/Reposea_container_optimizer

---

## Overview

RepoSea is a stateful data pipeline that simulates and processes container logistics data through a medallion architecture (Bronze, Silver, Gold), producing a live dashboard and analytics-ready exports for Power BI and Tableau.

```
AIS Feed + EDI 322 + ERP  -->  Bronze  (stateful fleet tick)
                                   |
       Dedup, LOCODE fix, merge -->  Silver  (clean and merged)
                                            |
            Buffer, status, burn rate  -->  Gold
                                                |
                  ---------------------------------------------
                  |                            |                |
             Dashboard                    Power BI export   Parquet export
             (GitHub Pages)                (Excel)           (Tableau)
```

---

## Fork & Run

1. Fork this repository.
2. Go to Settings -> Actions -> General -> Workflow permissions -> select "Read and write" -> Save.
3. Go to Settings -> Pages -> Source -> select "GitHub Actions" -> Save.
4. Go to Actions -> run the "Fork Setup" workflow.

The live dashboard will be available at `https://<your-username>.github.io/<repo-name>/` within about two minutes.

For a full step-by-step walkthrough, open `docs/SETUP_GUIDE.html` in a browser.

---

## Project Structure

```
reposea/
├── .github/workflows/
│   ├── fork_setup.yml          Runs once on fork, configures the repo
│   └── pipeline.yml            Hourly cron: Bronze -> Silver -> Gold -> Excel -> Pages
│
├── pipeline/
│   ├── state_manager.py        Persistent fleet state (lifecycle engine)
│   ├── bronze_ingest.py        Stateful AIS + EDI 322 + ERP ingestion
│   ├── silver_clean.py         LOCODE standardization, dedup, merge
│   ├── gold_aggregate.py       Buffer calculation, status flags, burn rate, priority
│   ├── export_excel.py         Formatted multi-sheet Power BI workbook
│   └── run_pipeline.py         Orchestrator entry point
│
├── dashboard/
│   └── index.html              RepoSea live dashboard (GitHub Pages)
│
├── data/
│   ├── state/fleet_state.json       Persistent fleet state (survives hourly runs)
│   ├── gold/containers.json         Dashboard + Power BI web connector source
│   ├── gold/summary.json            KPI aggregates
│   └── exports/reposea_report.xlsx  Multi-sheet Power BI workbook
│
├── warehouse/
│   └── bigquery_load.py        Loads Bronze/Silver/Gold into BigQuery (medallion datasets)
│
├── airflow/dags/
│   └── reposea_medallion_dag.py  Daily DAG: extract -> transform -> BigQuery -> dbt -> (weekly) ML retrain
│
├── docker/
│   ├── Dockerfile               Containerizes the pipeline
│   └── docker-compose.yml       Local pipeline container + standalone Airflow sandbox
│
├── dbt/reposea/
│   ├── models/staging/          stg_containers + source/schema tests
│   ├── models/marts/            mart_lane_performance, mart_container_status
│   └── seeds/                   trade_lane_dim, local dev seed (DuckDB target — no cloud creds needed)
│
├── ml/
│   ├── generate_training_data.py  Simulates historical voyages from real port_intel/market_rates constants
│   └── train_eta_model.py         XGBoost ETA-deviation regressor, time-based holdout, cost-impact conversion
│
├── analysis/
│   └── generate_narrative.py    Writes LANE_RELIABILITY_NARRATIVE.md from live Gold data
│
├── bi/
│   ├── POWERBI_STAR_SCHEMA_AND_DAX.md  Dimensional model, DAX measures, Publish-to-Web steps
│   └── TABLEAU_PUBLISH_SETUP.md        Tableau Public publishing steps
│
├── docs/
│   ├── SETUP_GUIDE.html        Interactive step-by-step setup walkthrough
│   └── POWER_BI_GUIDE.md       Power BI and Tableau connection guide
│
└── tests/
    └── test_pipeline.py        Pytest suite
```

---

## Portfolio Requirement Coverage

Status against `portfolio_project_requirements.md`'s Project 1 spec, so this
doesn't drift out of sync with what's actually built again:

| Layer | Requirement | Status | Where |
|---|---|---|---|
| `DE` | Bronze/Silver/Gold medallion in BigQuery | Automated — runs every hour alongside the existing pipeline once `GCP_SA_KEY_B64`/`BQ_PROJECT_ID` secrets are set (Step 6 of `docs/SETUP_GUIDE.html`); skipped gracefully if unset | `.github/workflows/pipeline.yml`, `warehouse/bigquery_load.py` |
| `DE` | Airflow DAG, daily extract → transform | Written as a reference orchestration artifact for a managed-Airflow environment (Cloud Composer/MWAA/Astronomer) — **not** what runs this fork online. The actual online automation is the GitHub Actions cron above, which fits this project's free/serverless design | `airflow/dags/reposea_medallion_dag.py`, `docker/docker-compose.yml` (local demo only) |
| `DE` | Docker containerization | Done | `docker/Dockerfile` |
| `ML` | XGBoost ETA deviation regression, MAE/RMSE on time-based holdout, cost-impact conversion | Done and tested — MAE/RMSE reported against a naive baseline. Retrains automatically every Monday (not hourly — the target barely moves within a week); see `ml/README.md` for why the training data is generated rather than pulled from the live (currently deterministic) simulator | `.github/workflows/ml_retrain.yml`, `ml/` |
| `AE` | dbt staging + mart models, `not_null`/`relationships` tests, published docs | Done — `dbt build` passes 27/27 tests. Runs automatically every hour once the BigQuery secrets are set, and publishes docs to `/dbt-docs/` on the same GitHub Pages deploy as the dashboard | `.github/workflows/pipeline.yml`, `dbt/reposea/` |
| `BI` | Power BI star schema, DAX measures, published publicly | Model + measures documented; publishing itself is a manual, one-time step you do in Power BI Desktop/Service (not something GitHub Actions can do on your behalf) | `bi/POWERBI_STAR_SCHEMA_AND_DAX.md` |
| `DA` | Written narrative: worst lanes, repositioning action | Done — regenerates automatically every hour from live Gold data as part of the same pipeline run | `.github/workflows/pipeline.yml`, `analysis/generate_narrative.py` → `analysis/LANE_RELIABILITY_NARRATIVE.md` |

**Not yet done, and worth knowing before you present this as "done":**
- The live simulator's ETA is currently deterministic (no weather/congestion
  noise applied), so the ML model above is trained on a realistic generated
  dataset, not on `data/gold/` history. `ml/README.md` explains the gap and
  the specific code change (`pipeline/state_manager.py`) that would close it.
- `warehouse/bigquery_load.py` and the BigQuery/dbt steps in
  `.github/workflows/pipeline.yml` are written against your actual module
  names and validated for syntax, but not run end-to-end against a live GCP
  project (no credentials available in the environment this was built in) —
  worth a first manual `workflow_dispatch` run to confirm before trusting
  the hourly schedule with it.
- Publishing the Power BI report and/or Tableau workbook publicly is a
  manual, one-time action in each tool — GitHub Actions gets the data
  automated and ready; the actual "Publish to Web" click is yours to do
  once.

---

## Stateful Fleet Engine

Containers move through a real lifecycle instead of resetting on each run:

```
GATE_IN -> AT_SEA -> ARRIVED -> IN_FREE_DAYS -> OVERDUE -> COMPLETE -> replaced
```

Each hourly pipeline run advances every container by elapsed real time. ETAs count down, buffers shrink, penalties accrue, and completed voyages are replaced by new containers.

The initial fleet seeds with the following distribution: 12% Gate-In, 38% At Sea, 5% Arrived, 30% Free Days, 15% Overdue.

---

## Connecting Power BI / Tableau

| Method  | Source                                                                                  | Best for       |
|---------|------------------------------------------------------------------------------------------|----------------|
| Excel   | `data/exports/reposea_report.xlsx` (raw GitHub URL)                                       | Power BI Desktop |
| JSON    | `https://rahulmistry18.github.io/Reposea_container_optimizer/data/gold/containers.json`   | Web connectors |
| Parquet | `data/gold/containers.parquet` (raw GitHub URL)                                           | Tableau        |

Full guide: `docs/POWER_BI_GUIDE.md`

---

## Core Formula

```
Buffer (days) = Contract Last Free Day - Now

Buffer < 0   -> Critical  (penalty = per diem rate x days over)
Buffer <= 2  -> Warning   (less than 48 hours to act)
Buffer > 2   -> Safe      (monitor only)
```

---

## 2-Week Overdue Escalation Emails

Any container that's been past its Last Free Day for **14+ days** gets flagged
automatically. `pipeline/alerts.py` runs as Stage 4 of every pipeline run:

- Containers are routed to a person by trade lane (or a per-container
  override) via `pipeline/config/owners.json` — edit that file to point at
  your real team.
- Each owner gets **one digest email** per run listing every overdue
  container on their desk (container ID, vessel, destination, days overdue,
  accrued penalty, 7-day projected exposure, suggested action) — never one
  email per box.
- Once a container is flagged, it won't re-alert for 7 days (configurable
  via `RESEND_AFTER_DAYS` in `pipeline/alerts.py`), and it's cleared from
  tracking automatically once it's resolved (back under 14 days overdue).

**To actually send mail**, set these as repo secrets (Settings → Secrets and
variables → Actions):

| Secret          | Example                        |
|------------------|---------------------------------|
| `SMTP_HOST`      | `smtp.gmail.com`                |
| `SMTP_PORT`      | `587`                           |
| `SMTP_USER`      | `you@yourcompany.com`           |
| `SMTP_PASSWORD`  | an SMTP app password            |
| `SMTP_FROM`      | `reposea-alerts@yourcompany.com`|

Without those secrets set, alerts run in **DRY RUN** mode — the full email
content is printed to the pipeline logs but nothing is sent, so a fresh fork
never fails just because mail isn't configured yet.

Test it locally any time with:
```bash
PYTHONPATH=$(pwd) python -m pipeline.alerts
```

---

## Container Lifecycle & Case Closure

A container isn't tracked forever. Once it's been overdue long enough to
have received its 2-week escalation email plus a further **7-day grace
period** (21 days total), its case is treated as resolved.

Closure tracking is a two-tier ledger, so the two files can never drift
out of sync with each other:

- **Primary ledger** (`data/state/container_ledger.json`) — every
  container ever tracked, one record each, covering **both** active and
  resolved containers. Updated every run: active containers get their
  record refreshed with `status: "active"`; a container that closes gets
  `status: "resolved"`, a `completed_at` timestamp, and a `closure_reason`.
  This is the single source of truth.
- **Final ledger** (`data/state/completed_containers.json`) — resolved
  entries only. It's not written independently; every run it's **fetched**
  from the primary ledger by filtering to `status == "resolved"`. This is
  the audit-facing export for finance/ops.

On top of the ledger, a closed container also gets one last "Resolved"
entry in its dashboard history (so its journey view doesn't just stop
unexplained), is removed from the active fleet, and the **next batch
container** spawns in to take its place — fleet size stays constant.

This threshold is defined once in `pipeline/state_manager.py`
(`ESCALATION_THRESHOLD_DAYS` / `RESOLUTION_GRACE_DAYS`) and mirrors the
same 14-day trigger used by the escalation email in `pipeline/alerts.py`.

---

## No Conflicting Runs

Two workflows write to `main` (the hourly pipeline and the one-time setup
workflow) — they share a single concurrency group
(`reposea-git-write-${{ github.ref }}`), so GitHub queues them instead of
letting two runs push to the same branch at once. The setup workflow also
only does real work **once**: after its first successful run it writes
`.github/.setup_complete`, and every push after that is a no-op check that
exits immediately — it no longer fires alongside the hourly pipeline on
every ordinary code push. Both commit steps also `git pull --rebase`
before pushing and retry a few times as a second line of defense.

---

## Resetting the Fleet

The simulated fleet accumulates state (containers, history, alert tracking)
every run. To wipe it and start completely fresh:

**On GitHub (the reset button):** Actions tab → "RepoSea — Hourly Pipeline"
→ **Run workflow** → tick **reset** → Run workflow. This is manual only —
it never runs on the hourly schedule, only when you trigger it yourself.

**Locally:**
```bash
PYTHONPATH=$(pwd) python -m pipeline.reset          # dry run — shows what would be deleted
PYTHONPATH=$(pwd) python -m pipeline.reset --yes    # actually deletes it
```

This clears `data/state/`, `data/bronze/`, `data/silver/`, and `data/gold/`
— the next run seeds a brand-new fleet from zero.

---

## Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

CI runs the same test suite automatically on every pipeline run (see
`.github/workflows/pipeline.yml`) — a broken pipeline stage fails the run
before any bad data ever gets committed or deployed.

---

## Plugging in Real Data

Replace the following three functions in `pipeline/bronze_ingest.py`:

| Function                | Replace with                         |
|--------------------------|---------------------------------------|
| `build_ais_stream()`     | Real MarineTraffic / Spire API call   |
| `build_event_stream()`   | EDI 322 parser from S3 / SFTP         |
| `build_contract_stream()`| ERP SQL query                          |

No changes are required to Silver, Gold, Excel, or the dashboard.

---

## License

MIT
