-- Research Pilotの分析母集団。
-- `$as_of_date`以前の最終取引日について、その時点までに利用できた財務だけを使う。
WITH pbr_at_date AS (
    SELECT *
    FROM point_in_time_pbr_research
    WHERE as_of_date <= DATE '$as_of_date'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY security_key
        ORDER BY as_of_date DESC
    ) = 1
),
joined AS (
    SELECT
        p.*,
        s.company_name,
        s.market_code,
        s.market_name,
        s.sector_17_code,
        s.sector_17_name,
        s.sector_33_code,
        s.sector_33_name,
        s.product_category_code,
        f.fiscal_period_type,
        f.period_start AS financial_period_start,
        f.revenue,
        f.operating_income,
        f.net_income,
        f.total_assets,
        f.equity,
        f.operating_cash_flow,
        f.investing_cash_flow,
        f.annual_dividend_per_share
    FROM pbr_at_date AS p
    LEFT JOIN security_master AS s
      ON p.security_key = s.security_key
    LEFT JOIN financial_disclosure AS f
      ON p.disclosure_number = f.disclosure_number
     AND p.security_key = COALESCE(f.jpx_code, f.target_key)
)
SELECT
    *,
    CASE
        WHEN equity IS NULL OR equity = 0 OR net_income IS NULL THEN NULL
        WHEN financial_period_start IS NULL OR financial_period_end IS NULL THEN NULL
        WHEN date_diff('day', financial_period_start, financial_period_end) <= 0 THEN NULL
        ELSE net_income::DOUBLE / equity * 365
             / date_diff('day', financial_period_start, financial_period_end)
    END AS annualized_roe_proxy,
    CASE
        WHEN total_assets IS NULL OR total_assets = 0 OR equity IS NULL THEN NULL
        ELSE equity::DOUBLE / total_assets
    END AS equity_ratio,
    CASE
        WHEN revenue IS NULL OR revenue = 0 OR operating_income IS NULL THEN NULL
        ELSE operating_income::DOUBLE / revenue
    END AS operating_margin,
    CASE
        WHEN operating_cash_flow IS NULL THEN NULL
        ELSE operating_cash_flow > 0
    END AS operating_cf_positive,
    CASE
        WHEN operating_cash_flow IS NULL OR investing_cash_flow IS NULL THEN NULL
        ELSE operating_cash_flow + investing_cash_flow
    END AS free_cash_flow_proxy,
    date_diff('day', financial_disclosed_date, as_of_date) AS disclosure_age_days,
    date_diff('day', financial_period_end, as_of_date) AS financial_period_age_days,
    date_diff('day', financial_disclosed_date, as_of_date) > $stale_after_days
        AS is_stale_financial,
    pbr > 0 AND pbr < $pbr_threshold AS is_below_pbr_threshold
FROM joined
