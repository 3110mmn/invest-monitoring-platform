with actual as (
    select *
    from {{ ref('int_actual_metric_values') }}
), opening_forecast as (
    select
        a.security_key,
        a.fiscal_year_end,
        a.reporting_scope,
        a.accounting_standard,
        a.metric_key,
        f.metric_value as forecast_value,
        f.available_at as forecast_available_at,
        f.disclosure_number as forecast_disclosure_number
    from actual a
    join {{ ref('int_forecast_metric_values') }} f
      on a.security_key = f.security_key
     and a.fiscal_year_end = f.forecast_fiscal_year_end
     and a.reporting_scope = f.reporting_scope
     and a.accounting_standard = f.accounting_standard
     and a.metric_key = f.metric_key
     and f.available_at < a.available_at
), prior_actual as (
    select
        security_key,
        date_add(fiscal_year_end, interval 1 year) as next_fiscal_year_end,
        reporting_scope,
        accounting_standard,
        metric_key,
        metric_value as prior_value,
        available_at as prior_available_at,
        disclosure_number as prior_disclosure_number
    from actual
)
select
    a.*,
    f.forecast_value,
    f.forecast_available_at,
    f.forecast_disclosure_number,
    p.prior_value,
    p.prior_available_at,
    p.prior_disclosure_number
from actual a
left join opening_forecast f
  on a.security_key = f.security_key
 and a.fiscal_year_end = f.fiscal_year_end
 and a.reporting_scope = f.reporting_scope
 and a.accounting_standard = f.accounting_standard
 and a.metric_key = f.metric_key
left join prior_actual p
  on a.security_key = p.security_key
 and a.fiscal_year_end = p.next_fiscal_year_end
 and a.reporting_scope = p.reporting_scope
 and a.accounting_standard = p.accounting_standard
 and a.metric_key = p.metric_key
 and p.prior_available_at < a.available_at
