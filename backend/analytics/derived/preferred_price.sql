-- 1日1銘柄につき採用する観測を1つに決める。
--
-- 一次観測は上書きしないので、同じ日に複数の取得元の行が並存する。どれを採るかは
-- **利用側ではなくここで一度だけ決める**。Derivedの計算とAPIの読み出しが別々に
-- 優先順位を持つと、チャートに出る値と計算されたリターンが食い違う。
--
-- J-Quantsを優先する。公式で調整済み、訂正情報も持つため。yfinanceは、J-Quantsの
-- 契約プランが提供しない直近84日を埋める用途で、公式版が届いた時点で自動的に退く。
--
-- 調整済み価格だけを採る。未調整と混ぜると分割の前後で値が跳ねる。
--
-- **銘柄の同一性は `security_key` で表す。** `jpx_code` は日本の証券にしか無く、
-- syntheticなデモデータには無い。`target_key` は優先株などでNULLになりうる。片方だけを
-- 使うと、NULLが1つのグループへまとめられて別々の銘柄が畳まれる。両方がNULLの行は
-- 実データにもデモデータにも存在しない。
SELECT
    COALESCE(jpx_code, target_key) AS security_key,
    jpx_code,
    target_key,
    obs_date,
    raw_open_price,
    raw_high_price,
    raw_low_price,
    raw_close_price,
    raw_volume,
    turnover_value,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    price_basis,
    adjustment_factor,
    market_cap_million_yen,
    ex_rights_type,
    upper_limit_flag,
    lower_limit_flag,
    source_key,
    ingestion_run_id
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(jpx_code, target_key), obs_date
            ORDER BY CASE source_key WHEN 'jquants' THEN 0 ELSE 1 END, source_key
        ) AS source_rank
    FROM read_parquet($parquet_glob, hive_partitioning = true)
    WHERE price_basis = 'adjusted'
)
WHERE source_rank = 1
