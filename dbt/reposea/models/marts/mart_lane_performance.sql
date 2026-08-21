-- Mart: one row per trade lane — the exact aggregation the BI star schema's
-- "avg deviation / cost impact / capacity utilization" measures roll up from,
-- and what analysis/generate_narrative.py reads for the DA write-up.

with containers as (
    select * from {{ ref('stg_containers') }}
),

by_lane as (
    select
        trade_lane,
        trade_lane_label,
        count(*)                                              as container_count,
        sum(case when action_status = 'Critical' then 1 else 0 end) as critical_count,
        sum(case when action_status = 'Warning'  then 1 else 0 end) as warning_count,
        sum(case when action_status = 'Safe'     then 1 else 0 end) as safe_count,
        round(avg(days_to_last_free_day), 2)                  as avg_buffer_days,
        round(sum(accrued_penalty_usd), 2)                    as total_burn_usd,
        round(avg(accrued_penalty_usd), 2)                    as avg_burn_usd,
        round(sum(projected_exposure_7d_usd), 2)              as total_projected_7d_exposure_usd,
        max(pipeline_run_ts)                                  as as_of
    from containers
    group by 1, 2
)

select
    *,
    round(100.0 * critical_count / nullif(container_count, 0), 1) as pct_critical
from by_lane
order by pct_critical desc
