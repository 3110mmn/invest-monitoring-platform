-- 会社の通期営業利益予想が、実績からどれだけ外れたかを記述するDerived。
--
-- **予想は「実績が出る前に見えていたもの」だけを使う。** `point_in_time_forecast`が
-- 各予想に有効期間を与えているので、ここでは実績開示日以前の予想に限って突き合わせる。
-- この制約を外すと、期末直前の修正を「会社予想」として評価することになる。
--
-- **MAPEを使わない。** 実績営業利益を分母にすると、着地がゼロ近傍の企業で誤差が発散する。
-- 実測では中央値16.8%に対して最大70,800%で、750件が100%を超えた。最悪の例は予想7.09億円
-- に対して実績100万円で、分母が小さいだけの行が平均を支配していた。
--
-- 代わりに**売上高でスケールする**。売上は営業利益よりゼロ近傍になりにくく、企業規模の
-- 違いも吸収する。誤差は「売上の何%にあたる利益の読み違いか」として読む。
--
-- ```
-- error_pct_of_revenue = (実績営業利益 − 予想営業利益) / 売上高 × 100
-- ```
--
-- 符号を残すのは、上振れと下振れを区別するため。正なら予想より良かった（上振れ）。
-- 絶対値版も併置し、精度と方向を別々に集計できるようにする。
--
-- **実績と予想は同じ会計基準・同じ連結範囲で比べる。** 基準が違うと営業利益の定義が
-- 変わる。揃わない組み合わせは行を作らず、`comparison_status`で理由を返す。
WITH actual AS (
    -- 着地した通期実績。訂正開示があるので同一(企業×年度)で複数行になり得る。
    -- 最初に出た実績を使う。訂正後の値を使うと、当時見えていなかった数字で
    -- 予想を採点することになる。
    SELECT * EXCLUDE (actual_rank)
    FROM (
        SELECT
            COALESCE(jpx_code, target_key) AS security_key,
            fiscal_year_end,
            operating_income AS actual_operating_income,
            revenue AS actual_revenue,
            accounting_standard AS actual_accounting_standard,
            reporting_scope AS actual_reporting_scope,
            disclosed_date AS actual_disclosed_date,
            disclosure_number AS actual_disclosure_number,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(jpx_code, target_key), fiscal_year_end
                ORDER BY disclosed_date ASC, disclosure_number ASC
            ) AS actual_rank
        FROM financial_disclosure
        WHERE fiscal_period_type = 'FY'
          AND operating_income IS NOT NULL
    )
    WHERE actual_rank = 1
),
paired AS (
    SELECT
        f.security_key,
        f.jpx_code,
        f.target_key,
        f.fiscal_year_end,
        f.forecast_seq,
        f.as_of_date,
        f.superseded_at,
        f.forecast_operating_income,
        f.disclosed_date AS forecast_disclosed_date,
        f.disclosure_number AS forecast_disclosure_number,
        f.accounting_standard AS forecast_accounting_standard,
        f.reporting_scope AS forecast_reporting_scope,
        a.actual_operating_income,
        a.actual_revenue,
        a.actual_accounting_standard,
        a.actual_reporting_scope,
        a.actual_disclosed_date,
        a.actual_disclosure_number,
        -- 期内で最後に出た予想か。期初(forecast_seq=1)との比較に使う。
        ROW_NUMBER() OVER (
            PARTITION BY f.security_key, f.fiscal_year_end
            ORDER BY f.forecast_seq DESC
        ) AS latest_rank
    FROM point_in_time_forecast AS f
    JOIN actual AS a
      ON f.security_key = a.security_key
     AND f.fiscal_year_end = a.fiscal_year_end
    -- **実績が出る前に見えていた予想だけを残す。** ここが先読み防止の中核。
    WHERE f.as_of_date <= a.actual_disclosed_date
)
SELECT
    security_key,
    jpx_code,
    target_key,
    fiscal_year_end,
    forecast_seq,
    latest_rank = 1 AS is_latest_forecast,
    as_of_date,
    superseded_at,
    forecast_operating_income,
    actual_operating_income,
    actual_revenue,
    actual_operating_income - forecast_operating_income AS error_amount,
    CASE
        WHEN actual_revenue IS NULL OR actual_revenue <= 0 THEN NULL
        WHEN actual_accounting_standard IS DISTINCT FROM forecast_accounting_standard THEN NULL
        WHEN actual_reporting_scope IS DISTINCT FROM forecast_reporting_scope THEN NULL
        ELSE (actual_operating_income - forecast_operating_income)
             / CAST(actual_revenue AS DOUBLE) * 100
    END AS error_pct_of_revenue,
    CASE
        WHEN actual_revenue IS NULL OR actual_revenue <= 0 THEN NULL
        WHEN actual_accounting_standard IS DISTINCT FROM forecast_accounting_standard THEN NULL
        WHEN actual_reporting_scope IS DISTINCT FROM forecast_reporting_scope THEN NULL
        ELSE ABS(actual_operating_income - forecast_operating_income)
             / CAST(actual_revenue AS DOUBLE) * 100
    END AS abs_error_pct_of_revenue,
    CASE
        WHEN actual_operating_income > forecast_operating_income THEN 'beat'
        WHEN actual_operating_income < forecast_operating_income THEN 'miss'
        ELSE 'in_line'
    END AS outcome,
    CASE
        WHEN actual_revenue IS NULL OR actual_revenue <= 0 THEN 'revenue_unavailable'
        WHEN actual_accounting_standard IS DISTINCT FROM forecast_accounting_standard
            THEN 'accounting_standard_mismatch'
        WHEN actual_reporting_scope IS DISTINCT FROM forecast_reporting_scope
            THEN 'reporting_scope_mismatch'
        ELSE 'ok'
    END AS comparison_status,
    forecast_accounting_standard,
    actual_accounting_standard,
    forecast_reporting_scope,
    actual_reporting_scope,
    forecast_disclosed_date,
    actual_disclosed_date,
    forecast_disclosure_number,
    actual_disclosure_number
FROM paired
