with annual_disclosures as (
    select
        *,
        row_number() over (
            partition by security_key, fiscal_year_end, reporting_scope, accounting_standard
            order by available_at, disclosure_number
        ) as release_rank
    from {{ ref('stg_financial_disclosures') }}
    where fiscal_period_type = 'FY'
      and fiscal_year_end is not null
), forecasts as (
    select
        *,
        date_add(fiscal_year_end, interval 1 year) as forecast_fiscal_year_end,
        next_forecast_revenue as value_revenue,
        next_forecast_operating_income as value_operating_income,
        next_forecast_net_income as value_net_income
    from annual_disclosures
    where release_rank = 1
), metrics as (
    select *, 'revenue' as metric_key, value_revenue as metric_value from forecasts
    union all
    select *, 'operating_income', value_operating_income from forecasts
    union all
    select *, 'net_income', value_net_income from forecasts
)
select *
from metrics
where metric_value is not null
