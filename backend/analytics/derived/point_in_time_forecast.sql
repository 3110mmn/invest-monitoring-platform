-- 各時点で「その時見えていた会社予想」を一意に引けるようにするDerived。
--
-- 会社予想は評価ではなく、開示をそのまま並べ替えたDerivedである。予想が妥当かどうかは
-- Assessmentで判断する。ここは「いつ何が見えていたか」だけを確定させる。
--
-- **最新の予想を使わない。** 1つの決算期に対し、会社は期中に何度も通期予想を出し直す。
-- 実測では(企業×年度×期)の1,047グループで複数回の開示があり、最大5回だった。実例として
-- コード18210の2025年3月期は、通期営業利益予想を10億→25億→75億と修正している。
--
-- 最後の予想を「会社予想」として実績と比べると、期末直前の修正を使うことになる。実測で
-- MAPEが28.7%から17.0%へ下がった。4割の精度向上に見えるが、**全て後知恵である。**
-- したがってこの定義は予想を畳まず、各予想に有効期間を与える。
--
-- ```
-- as_of_date <= 基準日 < superseded_at   ← 基準日時点で見えていた予想が一意に決まる
-- ```
--
-- **`superseded_at`を持たせ、ROW_NUMBERで最新を取る形にしない。** 「最新」は基準日に
-- 依存するため、基準日を変えて再計算したときに静かに壊れる。有効期間なら、どの基準日でも
-- 同じ規則で引ける。
--
-- **同日開示は日付だけで順序が決まらない。** 実測で102行が同日に2件開示されていた。
-- 開示時刻と開示番号まで見て順序を決める。`point_in_time_pbr`と同じ規則を使う。
--
-- **予想が見えるのは翌取引日からとする。** 開示時刻と市場の前後を日付だけで判定できない
-- ため、`point_in_time_pbr`と同じ保守側へ倒す。`as_of_date`は開示日の翌日である。
--
-- 対象は通期の営業利益予想に限る。銀行は営業利益を開示しない（実測で銀行7社19開示の
-- うち営業利益は0件）ため、結果として対象外になる。欠損は0で埋めず、行を作らない。
WITH forecast_disclosure AS (
    -- 通期予想を載せる開示を1本に集める。四半期決算短信・通期決算短信・予想修正の
    -- いずれにも載るため、document_typeでは絞らない。
    --
    -- 通期決算短信(FY)の`forecast_operating_income`は**着地した期の予想**であり、
    -- 実績と同時に出るので予想として使えない。FY開示から取るのは翌期予想
    -- (`next_forecast_operating_income`)だけにする。
    SELECT
        COALESCE(jpx_code, target_key) AS security_key,
        jpx_code,
        target_key,
        fiscal_year_end,
        disclosed_date,
        disclosed_time,
        disclosure_number,
        document_type,
        accounting_standard,
        reporting_scope,
        forecast_operating_income AS forecast_operating_income,
        ingestion_run_id
    FROM financial_disclosure
    WHERE forecast_operating_income IS NOT NULL
      AND fiscal_period_type IN ('1Q', '2Q', '3Q')

    UNION ALL

    -- 通期決算短信に載る翌期予想。対象年度は翌期なので`fiscal_year_end`をずらす。
    -- 期末日は会社ごとに異なるため固定で365日足さず、翌期の開示側と突き合わせる
    -- 基準として`next_fiscal_year_end`を持たせる。
    SELECT
        COALESCE(jpx_code, target_key) AS security_key,
        jpx_code,
        target_key,
        next_fiscal_year_end AS fiscal_year_end,
        disclosed_date,
        disclosed_time,
        disclosure_number,
        document_type,
        accounting_standard,
        reporting_scope,
        next_forecast_operating_income AS forecast_operating_income,
        ingestion_run_id
    FROM (
        SELECT
            *,
            -- 翌期の期末日。決算期変更を考慮せず1年後とみなす。ずれた場合は
            -- 実績との突合で対象年度が一致せず、その行が落ちる（誤った年度へ
            -- 紐づくより、落ちる方を選ぶ）。
            CAST(fiscal_year_end + INTERVAL 1 YEAR AS DATE) AS next_fiscal_year_end
        FROM financial_disclosure
        WHERE next_forecast_operating_income IS NOT NULL
          AND fiscal_period_type = 'FY'
    )
),
ordered AS (
    SELECT
        *,
        -- 同日開示の順序。連結を優先し、次に開示時刻、最後に開示番号。
        -- `point_in_time_pbr`と同じ規則にする。片方だけ変えると、同じ開示から
        -- 違う値が出る。
        ROW_NUMBER() OVER (
            PARTITION BY security_key, fiscal_year_end, disclosed_date
            ORDER BY
                CASE reporting_scope WHEN 'consolidated' THEN 0 ELSE 1 END,
                disclosed_time DESC NULLS LAST,
                disclosure_number DESC
        ) AS same_day_rank
    FROM forecast_disclosure
),
deduplicated AS (
    SELECT * EXCLUDE (same_day_rank)
    FROM ordered
    WHERE same_day_rank = 1
)
SELECT
    security_key,
    jpx_code,
    target_key,
    fiscal_year_end,
    -- 開示の翌日から利用可能とする。開示当日は使わない。
    CAST(disclosed_date + INTERVAL 1 DAY AS DATE) AS as_of_date,
    -- 次の予想が利用可能になる日。最後の予想はNULL（以降ずっと有効）。
    -- 基準日の判定は `as_of_date <= d AND (superseded_at IS NULL OR d < superseded_at)`。
    LEAD(CAST(disclosed_date + INTERVAL 1 DAY AS DATE)) OVER (
        PARTITION BY security_key, fiscal_year_end
        ORDER BY disclosed_date, disclosure_number
    ) AS superseded_at,
    -- 期内で何回目の予想か。1が期初予想。先読み検知の比較軸に使う。
    ROW_NUMBER() OVER (
        PARTITION BY security_key, fiscal_year_end
        ORDER BY disclosed_date, disclosure_number
    ) AS forecast_seq,
    forecast_operating_income,
    disclosed_date,
    disclosed_time,
    disclosure_number,
    document_type,
    accounting_standard,
    reporting_scope,
    ingestion_run_id
FROM deduplicated
