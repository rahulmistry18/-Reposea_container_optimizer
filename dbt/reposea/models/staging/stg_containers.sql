-- Staging: one row per container, light typing/renaming only.
-- No business logic here (that already happened in pipeline/gold_aggregate.py) —
-- this model exists so every downstream mart has one stable, tested contract
-- to build on regardless of whether the source is BigQuery (prod) or the
-- seed snapshot (dev).

with source as (
    select * from {{ get_gold_containers_relation() }}
),

renamed as (
    select
        container_id,
        contract_id,
        lease_type,
        trade_lane,
        trade_lane_label,
        vessel_name,
        imo_number,
        origin_locode,
        origin_label,
        dest_locode,
        dest_label,
        cast(eta_parsed as timestamp)          as eta_ts,
        cast(lfd_parsed as timestamp)          as last_free_day_ts,
        cast(days_to_lfd as {{ float_type() }})            as days_to_last_free_day,
        agreed_free_days,
        cast(per_diem_rate as {{ float_type() }})          as per_diem_rate_usd,
        cast(burn_rate_usd as {{ float_type() }})          as accrued_penalty_usd,
        action_status,
        priority_score,
        lifecycle_stage,
        cast(voyage_pct as {{ float_type() }})             as voyage_pct,
        cast(projected_exposure_7d_usd as {{ float_type() }}) as projected_exposure_7d_usd,
        repo_strategy,
        cast(pipeline_run_ts as timestamp)     as pipeline_run_ts
    from source
)

select * from renamed
