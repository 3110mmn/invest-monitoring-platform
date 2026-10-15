select *
from {{ ref('fct_metric_comparison') }}
where reference_available_at is not null
  and reference_available_at >= current_available_at

