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
├── docs/
│   ├── SETUP_GUIDE.html        Interactive step-by-step setup walkthrough
│   └── POWER_BI_GUIDE.md       Power BI and Tableau connection guide
│
└── tests/
    └── test_pipeline.py        Pytest suite
```

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

<<<<<<< HEAD
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


>>>>>>> 76e956aa78553138a7a7899222941f804c3e6f72
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
