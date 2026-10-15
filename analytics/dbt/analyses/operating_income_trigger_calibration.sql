-- Exploration only: calibrate a Review trigger from the reusable comparison Fact.
-- This query does not create an operational Rule or persist an Assessment.

{% set thresholds = [-5, -10, -20, -30, -50] %}

with base as (
    select
        security_key,
        fiscal_year_end,
        observed_value,
        raw_uri,
        response_sha256,
        source_record_hash
    from {{ ref('fct_metric_comparison') }}
    where metric_key = 'operating_income'
      and comparison_type = 'forecast_variance'
      and comparison_status = 'ok'
), thresholds as (
    {% for threshold in thresholds %}
    select cast({{ threshold }} as float64) as threshold
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)
select
    extract(year from fiscal_year_end) as fiscal_year,
    threshold,
    count(*) as evaluable,
    countif(observed_value <= threshold) as fired,
    safe_divide(countif(observed_value <= threshold), count(*)) as fired_rate,
    count(distinct if(observed_value <= threshold, security_key, null)) as fired_securities,
    countif(
        observed_value <= threshold
        and raw_uri is not null
        and response_sha256 is not null
        and source_record_hash is not null
    ) as fired_with_complete_lineage
from base
cross join thresholds
group by fiscal_year, threshold
order by fiscal_year, threshold desc
