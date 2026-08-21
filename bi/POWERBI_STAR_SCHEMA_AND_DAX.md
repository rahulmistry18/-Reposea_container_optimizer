# Power BI — Star Schema, DAX Measures & Publishing

This doc covers the two things `docs/POWER_BI_GUIDE.md` doesn't: the
**dimensional model** the dashboard should actually be built on (rather than
one flat table), and the **step-by-step to make it public**. For how to
connect Power BI to the data in the first place, see
`docs/POWER_BI_GUIDE.md`.

## Why a star schema instead of the flat `Containers` sheet

The Excel export and Gold JSON are both one flat table — fine for a quick
connect, but every measure below either double-counts or can't do
lane/vessel/date slicing cleanly against a flat table once you add a
date/trend dimension. Model it as a star instead:

```
                    Dim_Date
                       │
Dim_TradeLane ─┐       │       ┌─ Dim_Vessel
               │       │       │
               └── Fact_ContainerStatus ──┘
               │                          │
        Dim_Port (Origin)          Dim_Port (Dest)
               │                          │
               └────── Dim_LeaseType ─────┘
```

| Table | Grain / contents | Source |
|---|---|---|
| `Fact_ContainerStatus` | one row per container per pipeline run | `mart_container_status` (dbt) or the Gold `Containers` sheet |
| `Dim_TradeLane` | `trade_lane`, `trade_lane_label`, `region_pair` | `dbt/reposea/seeds/trade_lane_dim.csv` |
| `Dim_Vessel` | `vessel_name`, `imo_number` | distinct values from Fact |
| `Dim_Port` | `locode`, `label` — used twice (role-playing dimension) for origin and destination | distinct values from Fact |
| `Dim_LeaseType` | `lease_type` | distinct values from Fact |
| `Dim_Date` | standard date table, marked as a Date table, joined on `pipeline_run_ts` | generated in Power BI (`CALENDAR()`) |

**Build steps in Power BI Desktop:**
1. Get Data → load `Fact_ContainerStatus` from the `Containers` sheet of
   `reposea_report.xlsx` (or from `mart_container_status` if you've built
   the dbt project against BigQuery — cleaner, already deduplicated).
2. Model view → for each dimension table, right-click → **New Table**, or
   pull distinct values via `Dim_TradeLane = DISTINCT(Fact_ContainerStatus[trade_lane])` etc.
   Since `Dim_TradeLane` already exists as a real dimension in the dbt seed,
   prefer loading that seed directly over deriving distincts.
3. For `Dim_Port`, load once and create **two relationships** to Fact
   (`origin_locode`, `dest_locode`) — mark one **Active**, the other
   **Inactive**, and reference the inactive one in DAX with `USERELATIONSHIP()`.
4. Create `Dim_Date` via Modeling → New Table:
   ```
   Dim_Date = CALENDAR(MIN(Fact_ContainerStatus[pipeline_run_ts]), MAX(Fact_ContainerStatus[pipeline_run_ts]))
   ```
   Mark it as a date table (Modeling → Mark as Date Table → pick the `Date` column).

## DAX measures

```dax
Avg Buffer Days =
AVERAGE ( Fact_ContainerStatus[days_to_lfd] )

Total Cost Impact =
SUM ( Fact_ContainerStatus[burn_rate_usd] )

Total Projected 7d Exposure =
SUM ( Fact_ContainerStatus[projected_exposure_7d_usd] )

Buffer Days Trend (7-run avg) =
AVERAGEX (
    DATESINPERIOD ( Dim_Date[Date], MAX ( Dim_Date[Date] ), -7, DAY ),
    CALCULATE ( [Avg Buffer Days] )
)

% Critical Fleet =
DIVIDE (
    CALCULATE ( COUNTROWS ( Fact_ContainerStatus ), Fact_ContainerStatus[action_status] = "Critical" ),
    COUNTROWS ( Fact_ContainerStatus )
)

-- "Capacity utilization" proxy: RepoSea has no terminal-slot-capacity field,
-- so this reads as the share of the fleet actively deployed (at sea or
-- gated in) vs. sitting resolved/idle — labeled as a proxy on the visual,
-- not presented as a true slot-capacity number.
Capacity Utilization % =
DIVIDE (
    CALCULATE (
        COUNTROWS ( Fact_ContainerStatus ),
        Fact_ContainerStatus[lifecycle_stage] IN { "AT_SEA", "GATE_IN" }
    ),
    COUNTROWS ( Fact_ContainerStatus )
)

Avg Priority Score =
AVERAGE ( Fact_ContainerStatus[priority_score] )
```

Suggested visuals: KPI cards for `% Critical Fleet` / `Total Cost Impact`;
a line chart of `Buffer Days Trend` by `Dim_Date`; a bar chart of
`Total Cost Impact` by `Dim_TradeLane[trade_lane_label]`; a matrix of
`Fact_ContainerStatus` sorted by `priority_score` for the drill-through
triage table the portfolio doc calls for.

## Publishing publicly

1. **File → Publish → Publish to Power BI** (needs a free Power BI account).
2. In Power BI Service, open the published report → **File → Publish to web
   (public)**. Confirm the public-embed warning — this makes the report
   viewable by anyone with the link, with no login required.
3. Copy the generated `<iframe>` embed link and the direct report URL —
   the direct URL is what goes in your portfolio/README as the
   "Public Power BI dashboard link."
4. Set **Scheduled Refresh** (Dataset → Settings → Scheduled Refresh →
   Hourly) so the public report tracks the live pipeline instead of going
   stale the day after you publish it.

**If your org/tenant disables Publish to Web:** record a short screen
capture of the report (Windows Snipping Tool / macOS Screen Recording,
30–60s panning through each visual) and host it as an unlisted YouTube
link or an MP4 in `docs/media/`, then link that instead — call it out
explicitly in the README as "Publish to Web disabled by org policy;
recorded walkthrough" so it doesn't read as a missing deliverable.
