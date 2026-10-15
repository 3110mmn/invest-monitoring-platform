-- Diagnostic only. The available overlap is too small to claim predictive power.

with forecast_variance as (
    select security_key, fiscal_year_end, observed_value
    from {{ ref('fct_metric_comparison') }}
    where metric_key = 'operating_income'
      and comparison_type = 'forecast_variance'
      and comparison_status = 'ok'
), yoy as (
    select security_key, fiscal_year_end, observed_value
    from {{ ref('fct_metric_comparison') }}
    where metric_key = 'operating_income'
      and comparison_type = 'yoy'
      and comparison_status = 'ok'
), paired as (
    select
        current.security_key,
        current.fiscal_year_end,
        current.observed_value as forecast_variance,
        next_year.observed_value as next_year_yoy
    from forecast_variance current
    left join yoy next_year
      on current.security_key = next_year.security_key
     and date_add(current.fiscal_year_end, interval 1 year) = next_year.fiscal_year_end
), thresholds as (
    select threshold from unnest([-10.0, -20.0, -30.0, -50.0]) as threshold
)
select
    threshold,
    countif(forecast_variance <= threshold) as fired,
    countif(forecast_variance <= threshold and next_year_yoy is not null) as fired_with_next_year,
    avg(if(forecast_variance <= threshold, next_year_yoy, null)) as fired_next_year_yoy_mean,
    avg(if(forecast_variance > threshold, next_year_yoy, null)) as not_fired_next_year_yoy_mean
from paired
cross join thresholds
group by threshold
order by threshold desc
