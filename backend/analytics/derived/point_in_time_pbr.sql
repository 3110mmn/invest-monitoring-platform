-- 各取引日時点で利用できた最新の財務開示を使うPBR。
--
-- PBRは評価ではなく、Observedから機械的に再計算できるDerivedである。
-- `is_below_book_value` も「0 < PBR < 1」という事実フラグに限り、割安かどうかは
-- Assessmentで判断する。
--
-- **調整済み終値 / 開示BPS を直接計算しない。** AdjCは後の株式分割を過去へ遡って
-- 反映する一方、BPSは開示対象期末の株数基準である。ここでは当日の未調整終値を使い、
-- 期末後から価格日までのAdjFactorをBPSへ掛けて、同じ株数基準へ揃える。
--
-- **同日開示を同日の終値へ使わない。** 開示時刻と市場終値の前後を日付だけでは完全に
-- 判定できないため、翌取引日から利用可能とする保守的な規則を採る。
--
-- 実データはJ-Quantsだけを対象にする。公開用synthetic demoは分割なし・調整係数1として同じ
-- 定義を通す。yfinanceのrawには未調整終値と日次調整係数を保存しておらず、推測で埋めると
-- 分割時に静かに誤る。計算不能は0で埋めず、calculation_statusで理由を返す。
WITH factor_curve AS (
    SELECT
        security_key,
        jpx_code,
        target_key,
        obs_date,
        raw_close_price,
        close_price AS adjusted_close_price,
        adjustment_factor,
        source_key AS price_source_key,
        ingestion_run_id AS price_ingestion_run_id,
        MIN(obs_date) OVER factor_window AS factor_coverage_start,
        SUM(
            CASE WHEN adjustment_factor IS NULL OR adjustment_factor <= 0 THEN 1 ELSE 0 END
        ) OVER factor_window AS invalid_factor_count,
        EXP(
            SUM(
                LN(
                    CASE
                        WHEN adjustment_factor IS NOT NULL AND adjustment_factor > 0
                        THEN adjustment_factor
                        ELSE 1
                    END
                )
            ) OVER factor_window
        ) AS cumulative_adjustment_factor
    FROM preferred_price
    WHERE source_key IN ('jquants', 'demo')
    WINDOW factor_window AS (
        PARTITION BY security_key
        ORDER BY obs_date
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )
),
financial_per_day AS (
    SELECT * EXCLUDE (same_day_rank)
    FROM (
        SELECT
            COALESCE(jpx_code, target_key) AS security_key,
            disclosure_number,
            disclosed_date,
            disclosed_time,
            period_end,
            accounting_standard,
            reporting_scope,
            bps,
            ingestion_run_id AS financial_ingestion_run_id,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(jpx_code, target_key), disclosed_date
                ORDER BY
                    CASE reporting_scope WHEN 'consolidated' THEN 0 ELSE 1 END,
                    disclosed_time DESC NULLS LAST,
                    disclosure_number DESC
            ) AS same_day_rank
        FROM financial_disclosure
        WHERE bps IS NOT NULL
    )
    WHERE same_day_rank = 1
),
price_with_financial AS (
    SELECT
        p.*,
        f.disclosure_number,
        f.disclosed_date AS financial_disclosed_date,
        f.disclosed_time AS financial_disclosed_time,
        f.period_end AS financial_period_end,
        f.accounting_standard,
        f.reporting_scope,
        f.bps AS reported_bps,
        f.financial_ingestion_run_id
    FROM factor_curve AS p
    ASOF LEFT JOIN financial_per_day AS f
        ON p.security_key = f.security_key
       AND p.obs_date > f.disclosed_date
),
aligned AS (
    SELECT
        current.*,
        period_factor.cumulative_adjustment_factor AS period_end_adjustment_factor,
        period_factor.invalid_factor_count AS period_end_invalid_factor_count
    FROM price_with_financial AS current
    ASOF LEFT JOIN factor_curve AS period_factor
        ON current.security_key = period_factor.security_key
       AND current.financial_period_end >= period_factor.obs_date
)
SELECT
    security_key,
    jpx_code,
    target_key,
    obs_date AS as_of_date,
    raw_close_price,
    adjusted_close_price,
    reported_bps,
    CASE
        WHEN period_end_adjustment_factor IS NULL THEN NULL
        WHEN invalid_factor_count > period_end_invalid_factor_count THEN NULL
        ELSE cumulative_adjustment_factor / period_end_adjustment_factor
    END AS bps_adjustment_factor,
    CASE
        WHEN reported_bps <= 0 THEN NULL
        WHEN period_end_adjustment_factor IS NULL THEN NULL
        WHEN invalid_factor_count > period_end_invalid_factor_count THEN NULL
        ELSE reported_bps * cumulative_adjustment_factor / period_end_adjustment_factor
    END AS adjusted_bps,
    CASE
        WHEN raw_close_price IS NULL OR raw_close_price <= 0 THEN NULL
        WHEN reported_bps IS NULL OR reported_bps <= 0 THEN NULL
        WHEN period_end_adjustment_factor IS NULL THEN NULL
        WHEN invalid_factor_count > period_end_invalid_factor_count THEN NULL
        ELSE raw_close_price
             / (reported_bps * cumulative_adjustment_factor / period_end_adjustment_factor)
    END AS pbr,
    CASE
        WHEN raw_close_price IS NULL OR raw_close_price <= 0 THEN NULL
        WHEN reported_bps IS NULL OR reported_bps <= 0 THEN NULL
        WHEN period_end_adjustment_factor IS NULL THEN NULL
        WHEN invalid_factor_count > period_end_invalid_factor_count THEN NULL
        ELSE raw_close_price
             / (reported_bps * cumulative_adjustment_factor / period_end_adjustment_factor)
             < 1
    END AS is_below_book_value,
    CASE
        WHEN raw_close_price IS NULL OR raw_close_price <= 0 THEN 'invalid_raw_close'
        WHEN disclosure_number IS NULL THEN 'financial_not_available'
        WHEN reported_bps <= 0 THEN 'non_positive_bps'
        WHEN financial_period_end IS NULL THEN 'financial_period_end_missing'
        WHEN factor_coverage_start > financial_period_end
          OR period_end_adjustment_factor IS NULL THEN 'insufficient_factor_history'
        WHEN invalid_factor_count > period_end_invalid_factor_count
          THEN 'invalid_adjustment_factor'
        ELSE 'ok'
    END AS calculation_status,
    disclosure_number,
    financial_disclosed_date,
    financial_disclosed_time,
    financial_period_end,
    accounting_standard,
    reporting_scope,
    price_source_key,
    price_ingestion_run_id,
    financial_ingestion_run_id
FROM aligned
