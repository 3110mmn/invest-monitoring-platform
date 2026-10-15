-- Exploration only: enrich fired operating-income Review triggers with the
-- EDINET DB retail pilot. EDINET annual reports can become available after the
-- trigger disclosure, so this query exposes both timestamps rather than
-- treating the annual report as evidence available at trigger time.

with fired as (
    select
        security_key,
        fiscal_year_end,
        reference_value as forecast_operating_income,
        current_value as actual_operating_income,
        observed_value as forecast_variance_pct,
        current_available_at as trigger_evidence_available_at,
        current_disclosure_number,
        reference_disclosure_number
    from {{ ref('fct_metric_comparison') }}
    where metric_key = 'operating_income'
      and comparison_type = 'forecast_variance'
      and comparison_status = 'ok'
      and observed_value <= -20
), retail as (
    select distinct
        edinet_code,
        substr(security_code, 1, 4) as code4,
        security_code,
        security_name,
        industry
    from {{ source('invest_staging', 'security_master') }}
), annual as (
    select
        retail.security_name,
        retail.code4,
        financial.*,
        lag(revenue) over company_history as previous_revenue,
        lag(gross_profit) over company_history as previous_gross_profit,
        lag(selling_general_admin_expenses) over company_history as previous_sga,
        lag(operating_income) over company_history as previous_operating_income,
        lag(net_income) over company_history as previous_net_income,
        lag(operating_cash_flow) over company_history as previous_operating_cash_flow
    from {{ source('invest_staging', 'financial_actual') }} as financial
    inner join retail using (edinet_code)
    where financial.reporting_scope = 'consolidated'
    window company_history as (
        partition by financial.edinet_code, financial.reporting_scope
        order by financial.fiscal_year
    )
)
select
    annual.security_name,
    fired.security_key,
    annual.edinet_code,
    annual.fiscal_year,
    fired.forecast_operating_income,
    fired.actual_operating_income,
    fired.forecast_variance_pct,
    100 * safe_divide(
        annual.revenue - annual.previous_revenue,
        annual.previous_revenue
    ) as revenue_yoy_pct,
    100 * safe_divide(annual.gross_profit, annual.revenue) as gross_margin_pct,
    100 * safe_divide(
        annual.previous_gross_profit,
        annual.previous_revenue
    ) as previous_gross_margin_pct,
    100 * safe_divide(annual.selling_general_admin_expenses, annual.revenue) as sga_ratio_pct,
    100 * safe_divide(annual.operating_income, annual.revenue) as operating_margin_pct,
    100 * safe_divide(
        annual.previous_operating_income,
        annual.previous_revenue
    ) as previous_operating_margin_pct,
    100 * safe_divide(
        annual.operating_income - annual.previous_operating_income,
        annual.previous_operating_income
    ) as operating_income_yoy_pct,
    100 * safe_divide(
        annual.net_income - annual.previous_net_income,
        abs(annual.previous_net_income)
    ) as net_income_change_pct,
    100 * safe_divide(
        annual.operating_cash_flow - annual.previous_operating_cash_flow,
        abs(annual.previous_operating_cash_flow)
    ) as operating_cash_flow_change_pct,
    annual.operating_cash_flow + annual.investing_cash_flow as free_cash_flow,
    fired.trigger_evidence_available_at,
    annual.available_at as annual_report_available_at,
    annual.available_at <= fired.trigger_evidence_available_at as available_at_trigger_time,
    annual.document_id,
    fired.current_disclosure_number,
    fired.reference_disclosure_number,
    annual.raw_uri,
    annual.response_sha256
from annual
inner join fired
  on replace(fired.security_key, '.T', '') = annual.code4
 and extract(year from fired.fiscal_year_end) = annual.fiscal_year
order by fired.forecast_variance_pct
