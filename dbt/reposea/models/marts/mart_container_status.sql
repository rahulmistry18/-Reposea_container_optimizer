-- Mart: container-grain triage list, ranked by priority — the row-level
-- detail table behind the Power BI drill-through / repositioning worklist.

select
    container_id,
    contract_id,
    lease_type,
    trade_lane,
    trade_lane_label,
    vessel_name,
    origin_label,
    dest_label,
    days_to_last_free_day,
    per_diem_rate_usd,
    accrued_penalty_usd,
    projected_exposure_7d_usd,
    action_status,
    priority_score,
    lifecycle_stage,
    repo_strategy,
    pipeline_run_ts
from {{ ref('stg_containers') }}
order by priority_score desc
