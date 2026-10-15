select *
from {{ ref('fct_metric_comparison') }}
where comparison_status = 'ok'
  and (reporting_scope is null or accounting_standard is null)

