with base as (
    select * from {{ ref('int_comparison_reference') }}
), comparisons as (
    select *, 'forecast_variance' as comparison_type,
        forecast_value as reference_value,
        forecast_available_at as reference_available_at,
        forecast_disclosure_number as reference_disclosure_number
    from base
    union all
    select *, 'yoy', prior_value, prior_available_at, prior_disclosure_number from base
    union all
    select *, 'absolute', cast(null as numeric), cast(null as timestamp), cast(null as string) from base
    union all
    select *, 'sign_transition', prior_value, prior_available_at, prior_disclosure_number from base
)
select
    to_hex(sha256(concat(
        security_key, '|', cast(fiscal_year_end as string), '|', metric_key, '|',
        comparison_type, '|', coalesce(reporting_scope, '__unknown__'), '|',
        coalesce(accounting_standard, '__unknown__')
    ))) as comparison_key,
    security_key,
    jpx_code,
    fiscal_year_start,
    fiscal_year_end,
    'FY' as period_type,
    metric_key,
    comparison_type,
    cast(reference_value as float64) as reference_value,
    cast(metric_value as float64) as current_value,
    cast(metric_value - reference_value as float64) as change_amount,
    case
      when comparison_type in ('absolute', 'sign_transition') then cast(metric_value as float64)
      when reference_value > 0 then cast((metric_value - reference_value) / reference_value * 100 as float64)
      else null
    end as change_rate,
    case
      when comparison_type in ('absolute', 'sign_transition') then cast(metric_value as float64)
      when reference_value > 0 then cast((metric_value - reference_value) / reference_value * 100 as float64)
      else null
    end as observed_value,
    reference_available_at,
    available_at as current_available_at,
    case
      when reporting_scope is null or accounting_standard is null then 'basis_unknown'
      when fiscal_year_start is null then 'period_unknown'
      when date_diff(fiscal_year_end, fiscal_year_start, day) not between 350 and 380 then 'unsupported_period'
      when metric_value is null then 'current_missing'
      when comparison_type in ('forecast_variance', 'yoy', 'sign_transition')
           and reference_value is null then 'reference_missing'
      when comparison_type in ('forecast_variance', 'yoy') and reference_value <= 0 then 'reference_nonpositive'
      else 'ok'
    end as comparison_status,
    reporting_scope,
    accounting_standard,
    disclosure_number as current_disclosure_number,
    reference_disclosure_number,
    ingestion_run_id,
    raw_uri,
    response_sha256,
    source_record_hash
from comparisons
