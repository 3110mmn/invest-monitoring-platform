-- 低PBR候補を人間が確認するためのEvidence表。
-- 良否・割安・value trapのラベルは付けず、値と不足項目だけを返す。
SELECT
    security_key,
    jpx_code,
    target_key,
    company_name,
    market_code,
    market_name,
    sector_17_code,
    sector_17_name,
    sector_33_code,
    sector_33_name,
    product_category_code,
    fiscal_period_type,
    as_of_date,
    pbr,
    annualized_roe_proxy,
    equity_ratio,
    operating_margin,
    operating_cash_flow,
    operating_cf_positive,
    free_cash_flow_proxy,
    financial_disclosed_date,
    financial_period_start,
    financial_period_end,
    disclosure_age_days,
    financial_period_age_days,
    is_stale_financial,
    concat_ws(
        ', ',
        CASE WHEN annualized_roe_proxy IS NULL THEN 'annualized_roe_proxy' END,
        CASE WHEN equity_ratio IS NULL THEN 'equity_ratio' END,
        CASE WHEN operating_margin IS NULL THEN 'operating_margin' END,
        CASE WHEN operating_cash_flow IS NULL THEN 'operating_cash_flow' END,
        CASE WHEN free_cash_flow_proxy IS NULL THEN 'free_cash_flow_proxy' END,
        CASE WHEN company_name IS NULL THEN 'security_master' END
    ) AS missing_evidence,
    calculation_status,
    disclosure_number,
    price_source_key,
    price_ingestion_run_id,
    financial_ingestion_run_id
FROM pbr_research_base
WHERE calculation_status = 'ok'
  AND pbr > 0
  AND pbr < $pbr_threshold
ORDER BY pbr, security_key
