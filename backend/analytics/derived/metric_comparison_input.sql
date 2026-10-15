-- Trigger input contract. Formulae live here (later dbt), never in FastAPI routes or UI.
-- Only first annual releases and identical scope/accounting basis are comparable.
WITH annual AS (
    SELECT *, COALESCE(jpx_code, target_key) AS security_key,
           ROW_NUMBER() OVER (
               PARTITION BY COALESCE(jpx_code, target_key), fiscal_year_end,
                            reporting_scope, accounting_standard
               ORDER BY disclosed_date, disclosed_time NULLS LAST, disclosure_number
           ) AS first_release
    FROM financial_disclosure
    WHERE fiscal_period_type = 'FY'
      AND disclosed_date IS NOT NULL AND fiscal_year_end IS NOT NULL
), actual AS (
    SELECT * FROM annual WHERE first_release = 1
), aligned AS (
    SELECT a.*, CAST(a.disclosed_date + INTERVAL 1 DAY AS DATE) AS available_on,
           p.disclosed_date AS prior_disclosed_date,
           p.disclosure_number AS prior_disclosure_number,
           p.revenue AS prior_revenue,
           p.operating_income AS prior_operating_income,
           p.net_income AS prior_net_income,
           p.next_forecast_revenue AS reference_forecast_revenue,
           p.next_forecast_operating_income AS reference_forecast_operating_income,
           p.next_forecast_net_income AS reference_forecast_net_income
    FROM actual a
    LEFT JOIN annual p
      ON a.security_key = p.security_key
     AND a.fiscal_year_end = CAST(p.fiscal_year_end + INTERVAL 1 YEAR AS DATE)
     AND a.reporting_scope = p.reporting_scope
     AND a.accounting_standard = p.accounting_standard
     AND p.first_release = 1
     AND p.disclosed_date < a.disclosed_date
), metrics AS (
    SELECT *, 'revenue' AS metric_key, revenue AS current_value,
           prior_revenue AS prior_value, reference_forecast_revenue AS forecast_value FROM aligned
    UNION ALL
    SELECT *, 'operating_income', operating_income,
           prior_operating_income, reference_forecast_operating_income FROM aligned
    UNION ALL
    SELECT *, 'net_income', net_income,
           prior_net_income, reference_forecast_net_income FROM aligned
), comparisons AS (
    SELECT *, 'forecast_variance' AS comparison_type, forecast_value AS reference_value,
           CASE WHEN forecast_value > 0 AND current_value IS NOT NULL
                THEN (current_value - forecast_value) / CAST(forecast_value AS DOUBLE) * 100 END AS observed_value
    FROM metrics
    UNION ALL
    SELECT *, 'yoy', prior_value,
           CASE WHEN prior_value > 0 AND current_value IS NOT NULL
                THEN (current_value - prior_value) / CAST(prior_value AS DOUBLE) * 100 END
    FROM metrics
    UNION ALL
    SELECT *, 'absolute', NULL, CAST(current_value AS DOUBLE) FROM metrics
    UNION ALL
    SELECT *, 'sign_transition', prior_value, CAST(current_value AS DOUBLE) FROM metrics
)
SELECT security_key, target_key, fiscal_year_end, fiscal_year_start,
       disclosed_date AS actual_disclosed_date, available_on,
       disclosure_number AS actual_disclosure_number,
       ingestion_run_id AS actual_ingestion_run_id, source_key AS actual_source_key,
       source_record_hash AS actual_source_record_hash,
       reporting_scope, accounting_standard, prior_disclosed_date,
       prior_disclosure_number, metric_key, comparison_type,
       CAST(reference_value AS DOUBLE) AS reference_value,
       CAST(current_value AS DOUBLE) AS current_value,
       CAST(observed_value AS DOUBLE) AS observed_value,
       CASE
           WHEN reporting_scope IS NULL OR accounting_standard IS NULL THEN 'basis_unknown'
           WHEN fiscal_year_start IS NULL THEN 'period_unknown'
           WHEN date_diff('day', fiscal_year_start, fiscal_year_end) NOT BETWEEN 350 AND 380 THEN 'unsupported_period'
           WHEN current_value IS NULL THEN 'current_missing'
           WHEN comparison_type IN ('forecast_variance', 'yoy', 'sign_transition')
                AND reference_value IS NULL THEN 'reference_missing'
           WHEN comparison_type IN ('forecast_variance', 'yoy') AND reference_value <= 0 THEN 'reference_nonpositive'
           ELSE 'ok'
       END AS input_status
FROM comparisons
