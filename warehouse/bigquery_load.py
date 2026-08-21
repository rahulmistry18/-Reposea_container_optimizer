"""
BIGQUERY MEDALLION LOADER — bigquery_load.py
===============================================
Loads the local Bronze/Silver/Gold output of the pipeline into BigQuery,
mirroring the same medallion layers as three datasets:

    {project}.reposea_bronze.*   raw ingested streams (ais_feed, container_events, contracts)
    {project}.reposea_silver.*   cleaned/merged container records
    {project}.reposea_gold.*     business-ready containers + summary + market

Uses WRITE_TRUNCATE per table per run — each pipeline run is a full
medallion refresh, which matches how Bronze/Silver/Gold already regenerate
from scratch each hour (see pipeline/run_pipeline.py). Table partitioning
is on `pipeline_run_ts` / an ingestion-time equivalent so historical runs
aren't destroyed even though each load truncates the *current* snapshot
table — see `--snapshot-history` to also append into a time-partitioned
history table for the ML/DA layers to query trends from.

Auth: set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON key
(read/write BigQuery Data Editor + Job User on the target project), and
BQ_PROJECT_ID for the target project.

Run:
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
    export BQ_PROJECT_ID=your-gcp-project
    PYTHONPATH=$(pwd) python -m warehouse.bigquery_load
"""
import argparse
import json
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BQ_LOAD] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

LAYERS = {
    "reposea_bronze": [
        ("ais_feed", DATA / "bronze" / "ais_feed.json"),
        ("container_events", DATA / "bronze" / "container_events.json"),
        ("contracts", DATA / "bronze" / "contracts.json"),
    ],
    "reposea_silver": [
        ("merged_containers", DATA / "silver" / "merged.json"),
    ],
    "reposea_gold": [
        ("containers", DATA / "gold" / "containers.json"),
        ("summary", DATA / "gold" / "summary.json"),
        ("market", DATA / "gold" / "market.json"),
    ],
}


def _load_json_rows(path: Path):
    if not path.exists():
        log.warning(f"Missing (skipped): {path}")
        return None
    raw = json.loads(path.read_text())
    # summary.json / market.json are single objects, not row arrays — wrap them
    return raw if isinstance(raw, list) else [raw]


def load_all(project_id: str, location: str = "US", snapshot_history: bool = False):
    from google.cloud import bigquery
    from google.cloud.exceptions import NotFound

    client = bigquery.Client(project=project_id)

    for dataset_id, tables in LAYERS.items():
        dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
        try:
            client.get_dataset(dataset_ref)
        except NotFound:
            ds = bigquery.Dataset(dataset_ref)
            ds.location = location
            client.create_dataset(ds)
            log.info(f"Created dataset {dataset_id}")

        for table_name, path in tables:
            rows = _load_json_rows(path)
            if not rows:
                continue

            table_ref = dataset_ref.table(table_name)
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            ndjson = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
            job = client.load_table_from_file(
                __import__("io").BytesIO(ndjson), table_ref, job_config=job_config
            )
            job.result()
            log.info(f"Loaded {len(rows)} rows -> {dataset_id}.{table_name}")

            if snapshot_history and dataset_id == "reposea_gold" and table_name == "containers":
                _append_history_snapshot(client, project_id, dataset_id, rows)


def _append_history_snapshot(client, project_id, dataset_id, rows):
    """Appends today's Gold containers into a partitioned history table so
    trend queries (ETA reliability over time, DA layer) survive the
    WRITE_TRUNCATE refresh on the live `containers` table."""
    from google.cloud import bigquery

    table_id = f"{project_id}.{dataset_id}.containers_history"
    schema_hint = [
        bigquery.SchemaField("pipeline_run_ts", "TIMESTAMP"),
    ]
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        time_partitioning=bigquery.TimePartitioning(field="pipeline_run_ts"),
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    ndjson = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    job = client.load_table_from_file(__import__("io").BytesIO(ndjson), table_id, job_config=job_config)
    job.result()
    log.info(f"Appended {len(rows)} rows -> {table_id} (history)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("BQ_PROJECT_ID"))
    ap.add_argument("--location", default=os.environ.get("BQ_LOCATION", "US"))
    ap.add_argument("--snapshot-history", action="store_true",
                     help="Also append Gold containers into a time-partitioned history table")
    args = ap.parse_args()

    if not args.project:
        raise SystemExit("Set --project or BQ_PROJECT_ID env var")

    load_all(args.project, args.location, args.snapshot_history)


if __name__ == "__main__":
    main()
