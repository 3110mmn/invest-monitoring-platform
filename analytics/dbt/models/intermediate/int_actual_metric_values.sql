with annual as (
    select *,
        row_number() over (
            partition by security_key, fiscal_year_end, reporting_scope, accounting_standard
            order by available_at, disclosure_number
        ) as actual_rank
    from {{ ref('stg_financial_disclosures') }}
    where fiscal_period_type = 'FY'
      and fiscal_year_end is not null
), first_actual as (
    select * except (actual_rank)
    from annual
    where actual_rank = 1
)
select *, 'revenue' as metric_key, revenue as metric_value from first_actual
union all
select *, 'operating_income', operating_income from first_actual
union all
select *, 'net_income', net_income from first_actual

