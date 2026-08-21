# Tableau — Publishing Setup

RepoSea's own `docs/POWER_BI_GUIDE.md` already documents connecting
Tableau to the JSON/Parquet feeds — this doc picks up from "I have a
workbook" through to "there's a public link for my portfolio," since the
portfolio requirements only specify Power BI for RepoSea's BI deliverable.
Use this if you want a second public dashboard, or if your org blocks
Power BI's Publish to Web (see the fallback note in the Power BI doc) and
Tableau Public is your public-hosting option instead.

## 1. Build the workbook against the same star schema

Reuse the model from `bi/POWERBI_STAR_SCHEMA_AND_DAX.md` — same fact/dim
split, just built as Tableau relationships instead of a Power BI model:

1. **Connect → Text file / JSON file** → point at
   `https://rahulmistry18.github.io/Reposea_container_optimizer/data/gold/containers.json`
   (or the Parquet file per `docs/POWER_BI_GUIDE.md` Option C — faster for
   repeated refreshes).
2. Drag in `trade_lane_dim.csv` (from `dbt/reposea/seeds/`) as a second
   connection and relate it on `trade_lane` — this is your `Dim_TradeLane`
   equivalent, keeps lane labels consistent with the dbt/Power BI models
   instead of re-deriving them.
3. Build calculated fields mirroring the DAX measures:
   ```
   % Critical Fleet:
   SUM(IF [action_status] = "Critical" THEN 1 ELSE 0 END) / COUNT([container_id])

   Capacity Utilization % (proxy — no true slot-capacity field exists):
   SUM(IF [lifecycle_stage] = "AT_SEA" OR [lifecycle_stage] = "GATE_IN" THEN 1 ELSE 0 END)
     / COUNT([container_id])
   ```
4. Suggested sheets: KPI text tiles (Critical count, Total Cost Impact),
   a bar chart of burn rate by trade lane, a Gantt/timeline of
   `days_to_lfd` per container colored by `action_status`, and a
   detail table with `priority_score` sort for the triage view.

## 2. Publish to Tableau Public (free, public by design)

Tableau Public is the free-tier path — every workbook published there is
public and searchable, which is exactly the "public dashboard link"
requirement; there's no private-then-publish step like Power BI's.

1. Install **Tableau Public Desktop** (free, separate download from
   Tableau Desktop — same UI, saves only to Tableau Public).
2. **Server → Tableau Public → Save to Tableau Public As...**
3. Sign in / create a free Tableau Public account, give the workbook a
   name, and save.
4. Tableau uploads the workbook and opens its public profile page —
   **that URL is your public dashboard link.**
5. Data freshness: Tableau Public **does not support scheduled
   auto-refresh** against a live web data source on the free tier. Two
   honest options, pick one and say which in your README:
   - **Manual republish**: re-open the workbook periodically, hit
     **Data → Refresh**, then **Save to Tableau Public** again. Fine for a
     portfolio piece where "as of" timestamps matter more than true
     real-time.
   - **Tableau Server / Cloud (paid or org-provided)**: supports scheduled
     extract refresh against a live connection — only relevant if you
     have access through work/school, not something to promise on the
     free tier.

## 3. If publishing the extract instead of a live connection

For reliability (GitHub Pages/raw URLs occasionally rate-limit repeated
Tableau Public refresh attempts), publish with an **extract** rather than
a live connection: **Data → [source] → Extract Data** before publishing.
This bakes in a snapshot, so note the `pipeline_run_ts` value from that
extract as the "data as of" caption on the dashboard — don't let a static
extract imply it's live when it isn't.

## Where this fits in the README

Once published, add both links (Power BI public report + Tableau Public
profile, if you build both) next to each other under Project 1's
deliverable line, each labeled with which one auto-refreshes hourly vs.
which is manually republished — a reviewer skimming the README shouldn't
have to guess which one is live.
