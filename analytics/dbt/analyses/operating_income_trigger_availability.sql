-- Classify why the operating-income forecast comparison can or cannot be evaluated.

select
    comparison_status,
    count(*) as row_count,
    count(distinct security_key) as security_count,
    safe_divide(count(*), sum(count(*)) over ()) as row_rate
from {{ ref('fct_metric_comparison') }}
where metric_key = 'operating_income'
  and comparison_type = 'forecast_variance'
group by comparison_status
order by row_count desc
