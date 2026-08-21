"""
RepoSea — Daily Medallion DAG
===============================
Orchestrates the same Bronze -> Silver -> Gold pipeline that already runs
hourly via GitHub Actions (.github/workflows/pipeline.yml), plus the two
steps that only make sense on a scheduled orchestrator rather than a plain
cron-in-CI: loading into BigQuery, and (weekly) retraining the ETA
deviation model.

Deployment
----------
Local demo:
    docker compose -f docker/docker-compose.yml up airflow
    # DAG appears at http://localhost:8080 as `reposea_daily_medallion`

Production: point DAGS_FOLDER at this file from a managed Airflow
(Cloud Composer, MWAA, Astronomer) and set the Airflow Variables below.

Airflow Variables expected (Admin -> Variables):
    reposea_repo_path   absolute path to the repo root inside the worker
    bq_project_id       target BigQuery project

Why this DAG exists alongside the GitHub Actions cron: the Actions workflow
is great for "keep the public demo alive on a schedule" but has no concept
of task-level retries/backfill/lineage or a BigQuery load step. This DAG is
the "real warehouse" path; GitHub Actions remains the path that keeps the
public GitHub Pages dashboard + Excel export fresh.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.bash import BashOperator
from airflow.operators.python import BranchPythonOperator
from airflow.operators.empty import EmptyOperator

REPO_PATH = Variable.get("reposea_repo_path", default_var="/opt/reposea")
BQ_PROJECT = Variable.get("bq_project_id", default_var="reposea-demo")

default_args = {
    "owner": "rahul.mistari",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="reposea_daily_medallion",
    description="Bronze -> Silver -> Gold -> BigQuery -> dbt, daily",
    default_args=default_args,
    schedule_interval="0 3 * * *",  # 03:00 UTC daily
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["reposea", "medallion", "portfolio"],
) as dag:

    env_prefix = f"cd {REPO_PATH} && PYTHONPATH={REPO_PATH}"

    bronze = BashOperator(
        task_id="bronze_ingest",
        bash_command=f"{env_prefix} python -m pipeline.bronze_ingest",
    )

    silver = BashOperator(
        task_id="silver_clean",
        bash_command=f"{env_prefix} python -m pipeline.silver_clean",
    )

    gold = BashOperator(
        task_id="gold_aggregate",
        bash_command=f"{env_prefix} python -m pipeline.gold_aggregate",
    )

    export_excel = BashOperator(
        task_id="export_excel",
        bash_command=f"{env_prefix} python -m pipeline.export_excel",
    )

    load_bigquery = BashOperator(
        task_id="load_bigquery",
        bash_command=(
            f"{env_prefix} python -m warehouse.bigquery_load "
            f"--project {BQ_PROJECT} --snapshot-history"
        ),
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {REPO_PATH}/dbt/reposea && dbt build --target prod",
    )

    dbt_docs = BashOperator(
        task_id="dbt_docs_generate",
        bash_command=f"cd {REPO_PATH}/dbt/reposea && dbt docs generate --target prod",
    )

    def _is_retrain_day(**_):
        # Weekly retrain — Monday only — a daily XGBoost retrain on a slow-moving
        # ETA-deviation distribution would just add noise.
        return "retrain_eta_model" if datetime.utcnow().weekday() == 0 else "skip_retrain"

    retrain_gate = BranchPythonOperator(
        task_id="retrain_gate",
        python_callable=_is_retrain_day,
    )

    retrain_eta_model = BashOperator(
        task_id="retrain_eta_model",
        bash_command=(
            f"{env_prefix} python -m ml.generate_training_data --rows 4000 && "
            f"{env_prefix} python -m ml.train_eta_model"
        ),
    )

    skip_retrain = EmptyOperator(task_id="skip_retrain")

    bronze >> silver >> gold >> [export_excel, load_bigquery]
    load_bigquery >> dbt_build >> dbt_docs
    gold >> retrain_gate >> [retrain_eta_model, skip_retrain]
