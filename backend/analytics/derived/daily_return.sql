-- 日次価格リターン。Observed（価格）から再計算できるDerived。
--
-- J-Quantsの調整済み終値は株式分割・併合等を補正するが、現金配当を含まない。
-- したがって、これは配当再投資込みのTotal ReturnではなくPrice Returnである。
--
-- 計算定義がこのファイルであり、結果は正本ではない。materializeするのは、高コスト・
-- 複数用途で共有・過去に提示した判断のEvidence、のいずれかが成立してからでよい。
--
-- 入力は `preferred_price`。「同じ日に複数の取得元がある場合どれを採るか」と
-- 「調整済みだけを使う」はそちらの定義に置いてある。ここで重ねて決めない。
--
-- 前提と割り切り:
--   * 欠損はゼロで埋めない。終値がNULLの日はリターンもNULLにする
--   * 直前の「観測がある日」との比を取る。休場や上場停止で日付が飛んでも、暦日ではなく
--     観測の並びで前日を決める
--   * 銘柄の同一性は `security_key` で判定する。定義は preferred_price にある
--   * `$target_filter` はAPIが1銘柄だけ計算するときのpushdown用。CLIで全銘柄を
--     計算するときは runner.build_query が空文字へ置換する
SELECT
    security_key,
    jpx_code,
    target_key,
    obs_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    price_basis,
    source_key,
    ingestion_run_id,
    LAG(close_price) OVER w AS prev_close_price,
    LAG(obs_date) OVER w AS prev_obs_date,
    CASE
        WHEN LAG(close_price) OVER w IS NULL THEN NULL
        WHEN LAG(close_price) OVER w = 0 THEN NULL   -- 0除算を避ける
        WHEN close_price IS NULL THEN NULL
        ELSE close_price / LAG(close_price) OVER w - 1
    END AS daily_return
FROM preferred_price
$target_filter
WINDOW w AS (PARTITION BY security_key ORDER BY obs_date)
