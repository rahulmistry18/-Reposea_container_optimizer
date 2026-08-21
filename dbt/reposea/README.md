# RepoSea dbt project

Staging + mart models on top of the Gold layer, with `not_null`,
`unique`, `accepted_values`, and `relationships` tests. Two targets share
one set of models:

| Target | Warehouse | Needs | Use for |
|---|---|---|---|
| `dev` (default) | DuckDB, reads `seeds/gold_containers_seed.csv` | nothing | anyone cloning the repo — reviewers, CI |
| `prod` | BigQuery, reads `reposea_gold.containers` | `GOOGLE_APPLICATION_CREDENTIALS`, `BQ_PROJECT_ID` | the real hourly-refreshed warehouse, loaded by `warehouse/bigquery_load.py` |

A macro (`macros/get_gold_containers_relation.sql`) picks the right source
per target, so `stg_containers.sql` and everything downstream doesn't
change between environments.

## Models

```
stg_containers            one row per container, typed/renamed
 └─ mart_lane_performance  one row per trade lane — reliability + cost rollup
 └─ mart_container_status  one row per container — priority-ranked triage list
```

Plus a `trade_lane_dim` seed so `relationships` tests have something real
to check `trade_lane` against.

## Run it

```bash
cd dbt/reposea
python -m pip install dbt-core dbt-duckdb   # dev target only
export DBT_PROFILES_DIR=$(pwd)
dbt build --target dev                       # seeds + models + all tests
```

Against the real warehouse:
```bash
python -m pip install dbt-bigquery
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
export BQ_PROJECT_ID=your-gcp-project
dbt build --target prod
```

## Publishing the docs

```bash
dbt docs generate --target dev
dbt docs serve   # local preview at http://localhost:8080
```

To make the docs **public** without dbt Cloud: `dbt docs generate` writes
static HTML/JSON to `target/`. Copy `target/index.html`, `target/catalog.json`,
`target/manifest.json` into a `docs/dbt/` folder and enable it as a second
GitHub Pages source (or a `gh-pages` branch) — same mechanism the live
dashboard already uses for `dashboard/index.html`. The `dbt_docs_generate`
task in `airflow/dags/reposea_medallion_dag.py` regenerates these on every
run against `prod`, so wiring that copy step into CI keeps the published
docs in sync with the warehouse automatically.
