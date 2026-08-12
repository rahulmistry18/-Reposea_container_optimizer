"""
REPOSEA RESET — reset.py
================================================
Manually wipes every generated/simulated file so the next pipeline run
starts from a completely fresh fleet, fresh history, and fresh alert state.

This is deliberately NOT automatic — it only runs when you trigger it
yourself, either:

  Locally:
      python -m pipeline.reset --yes

  On GitHub Actions (the "reset button"):
      Actions tab -> "RepoSea — Hourly Pipeline" -> Run workflow
      -> tick "reset" -> Run workflow

What gets cleared:
  data/state/fleet_state.json        the simulated fleet (containers, stages)
  data/state/container_history.json  30-day journey snapshots
  data/state/alerts_sent.json        2-week escalation email dedup tracking
  data/state/*_cache.json            AIS / market / port intel caches
  data/bronze/*.json                 raw ingested streams (regenerated each run)
  data/silver/*.json, *.parquet      cleaned/merged streams (regenerated each run)
  data/gold/*.json, *.parquet        Gold output (regenerated each run)

What's kept: .gitkeep files, and everything outside data/ (code, dashboard,
config, docs).

Without --yes, this prints what it WOULD delete and exits — nothing is
touched until you confirm.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

TARGETS = [
    ROOT / "data" / "state" / "fleet_state.json",
    ROOT / "data" / "state" / "container_history.json",
    ROOT / "data" / "state" / "alerts_sent.json",
    ROOT / "data" / "state" / "container_ledger.json",
    ROOT / "data" / "state" / "completed_containers.json",
    ROOT / "data" / "state" / "ais_cache.json",
    ROOT / "data" / "state" / "market_cache.json",
    ROOT / "data" / "state" / "port_cache.json",
]

GLOB_TARGETS = [
    (ROOT / "data" / "bronze", "*.json"),
    (ROOT / "data" / "silver", "*.json"),
    (ROOT / "data" / "silver", "*.parquet"),
    (ROOT / "data" / "gold", "*.json"),
    (ROOT / "data" / "gold", "*.parquet"),
]


def _collect() -> list:
    files = [p for p in TARGETS if p.exists()]
    for directory, pattern in GLOB_TARGETS:
        if directory.exists():
            files.extend(sorted(directory.glob(pattern)))
    return files


def run(confirm: bool) -> int:
    files = _collect()

    if not files:
        print("Nothing to reset — no generated state files found.")
        return 0

    print(f"{'Deleting' if confirm else 'Would delete'} {len(files)} file(s):")
    for f in files:
        print(f"  {f.relative_to(ROOT)}")

    if not confirm:
        print("\nDry run only — nothing was deleted. Re-run with --yes to actually reset.")
        return 0

    for f in files:
        f.unlink()

    print(
        "\nReset complete. The next pipeline run will seed a brand-new fleet, "
        "start history from zero, and clear all pending escalation-email tracking."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe RepoSea's generated pipeline state")
    parser.add_argument("--yes", action="store_true", help="Actually delete files (default is dry run)")
    args = parser.parse_args()
    sys.exit(run(confirm=args.yes))


if __name__ == "__main__":
    main()
