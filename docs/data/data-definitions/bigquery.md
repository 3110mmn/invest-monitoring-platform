# BigQueryデータ定義

BigQueryはGCS rawの代替ではなく、複数年度・複数銘柄を検索、変換、検査する分析履歴層です。
PostgreSQLのRuleやユーザー入力も移しません。物理DDLの正本は
`backend/app/analytics/bigquery_financial_history.py`です。

## データセット

| Dataset | 責務 |
|---|---|
| `invest_raw` | GCS raw responseと取得来歴を改変せず保持する再ロード可能な着地点 |
| `invest_staging` | JSONを展開し、型と時点を明示した分析入力view |
| `invest_intermediate` | 指標値と比較対象を時点整合させるdbt view |
| `invest_marts` | Trigger Ruleから再利用できる比較Fact |

Dataset locationは既定で`australia-southeast1`です。BigQueryのdataset locationは作成後に
変更できないため、CLIの初回実行前に環境変数で確認します。

すべてのquery jobには`BIGQUERY_MAXIMUM_BYTES_BILLED`を適用し、既定では1クエリ10 GiBを超える
見込みの処理を実行前に拒否します。この上限はDDL、`MERGE`、将来の変換queryの安全装置です。
一方、GCSからのload job、streaming write、保存容量、プロジェクト全体の日次利用額には適用されません。
総量の上限はGCP側のquotaとBudget alertを別途設定します。

## `invest_raw.source_response`

粒度は、外部APIから保存した1 responseです。同じ`raw_object_key`を再ロードしても`MERGE`で
重複させません。GCSのオブジェクトとmanifestが原本であり、このテーブルは削除・再構築できます。

| Column | Type | 意味 |
|---|---|---|
| `raw_object_key` | STRING | `source:endpoint:unit_key:response_sha256` |
| `source` | STRING | 取得元。`edinetdb`または`jquants` |
| `endpoint` | STRING | `companies`、`financials`、`financial_summary` |
| `unit_key` | STRING | EDINET DBではEDINETコード、J-Quantsでは`ingestion_run_id` |
| `snapshot_date` | DATE | backfill snapshot日。partition key |
| `raw_uri` | STRING | GCS上のimmutable raw URI |
| `manifest_uri` | STRING | 取得単位manifestのURI |
| `response_sha256` | STRING | 非圧縮response bytesのSHA-256 |
| `fetched_at` | TIMESTAMP | APIから取得した時刻 |
| `loaded_at` | TIMESTAMP | BigQueryへロードした時刻 |
| `request_url` | STRING | 取得URL |
| `api_version` | STRING | responseに記録されたAPI version |
| `http_status` | INT64 | HTTP status |
| `record_count` | INT64 | response内の行数 |
| `min_fiscal_year` | INT64 | response内の最古年度 |
| `max_fiscal_year` | INT64 | response内の最新年度 |
| `ingestion_run_id` | INT64 | J-Quantsの取得実行ID。EDINET DBではNULL |
| `git_commit_sha` | STRING | 取得処理を実行したコードversion |
| `payload_json` | JSON | raw responseのJSON payload |

`source`、`endpoint`、`unit_key`でclusterします。rawのhashがmanifestと一致しなければロードを中止します。

## `invest_staging.security_master`

EDINET DBの`companies` responseを1社1行へ展開します。初期列はEDINETコード、証券コード、
名称、業種、上場状態、取得時点とlineageです。企業分類の正本をBigQueryで新設するのではなく、
取得時点の外部マスタ履歴として扱います。

## `invest_staging.financial_actual`

EDINET DBの`financials` responseを`企業 × 年度 × reporting scope × document`へ展開します。
売上高、売上原価、粗利益、販管費、各利益、資産、純資産、CFを初期対象とします。

`submit_date`（`YYYY-MM-DD HH:MM`の日本時間）をUTCの`available_at`へ変換して保持します。分析では`fiscal_year`だけで結合せず、評価時点より
後の`available_at`を参照しないことが必須です。値の意味が異なる連結／単体と会計基準も列として残します。
元responseに`submit_date`がない年度は推測補完せずNULLとし、point-in-time分析の対象外として扱います。

## `invest_staging.jquants_financial_disclosure`

J-Quantsの決算短信・会社予想履歴を、1開示1行へ展開します。実績と会社予想は元の開示行から
切り離さず、`disclosure_number`、`available_at`、`ingestion_run_id`、response hash、
raw URI、取得コードversionを保持します。同じ開示を複数回取得した場合は、開示番号をbusiness
keyとして最新の取込1件へ決定的に畳みます。訂正開示は別の開示番号なので失われません。

`available_at`はJ-Quantsの開示日・開示時刻を日本時間としてUTCへ変換します。開示時刻がない場合は、
その日の終了時刻を置き、当日の分析に使えない保守的な扱いにします。通期決算短信の翌期予想を
どの決算期へ対応させるか、会社予想と実績をどう比較するかはdbtで実装し、このstaging
viewでは評価しません。

ロード後は次を検査し、1件でも違反があればCLIを失敗させます。

- 開示番号・銘柄コード・開示日・利用可能時点の欠損
- 取得時刻より未来の開示を取得したことになっていないか
- 銘柄コード×開示番号の重複
- 会社予想があるのに決算期末がない行

## 初期ロード

```bash
cd backend
../venv/bin/python -m scripts.load_bigquery_financial_history \
  --manifests gs://invest-dwh-db-storage/raw/edinetdb/manifests
```

初期段階では価格、全Parquet、segment、expensesをロードしません。会社予想・決算短信履歴は
次のCLIで同じlineage契約へ載せます。

```bash
cd backend
../venv/bin/python -m scripts.load_jquants_bigquery
```

このCLIはPostgreSQLの`ingestion_run`をrawの索引として使います。GCS URIを正本とし、同一rawが
ローカルに残っている場合だけローカルを読み取りキャッシュとして使います。同一run・同一hashを
再ロードしても`MERGE`により重複しません。比較計算はdbt導入後の責務です。

### 実ロード検証（2026-10-13）

実データのJ-Quants財務raw 498 response、38,538 recordsをロードしました。開示番号で重複取得を
畳んだstagingは38,181開示です。同じ498 responseを再ロードしてもraw responseは498行のままで、
`raw_object_key`は498件すべて一意、raw URI・response hash・`ingestion_run_id`の欠損は0件でした。

初期充足数は、売上高実績28,654件、営業利益実績27,751件、純利益実績28,686件、当期会社予想を
1項目以上持つ開示23,069件、翌期会社予想を1項目以上持つ開示7,119件です。時点整合性検査4項目は
すべて違反0件でした。

## `invest_marts.fct_metric_comparison`

粒度は`銘柄 × 通期決算期 × 指標 × 比較方法 × 連結区分 × 会計基準`です。元開示を
上書きせず、Ruleが同じ計算結果を異なる閾値で評価するための再利用可能なDerived Factです。

| Column | Type | 意味 |
|---|---|---|
| `comparison_key` | STRING | 粒度列から生成した決定的なSHA-256 key |
| `security_key` | STRING | アプリと共有する`xxxx.T`形式の銘柄key |
| `fiscal_year_start` / `fiscal_year_end` | DATE | 比較対象の通期決算期間 |
| `metric_key` | STRING | `revenue`、`operating_income`、`net_income` |
| `comparison_type` | STRING | `forecast_variance`、`yoy`、`absolute`、`sign_transition` |
| `reference_value` / `current_value` | FLOAT64 | 比較基準値と当期実績値 |
| `change_amount` | FLOAT64 | 当期実績－比較基準値 |
| `change_rate` | FLOAT64 | 正の分母に対する変化率。絶対値・符号変化では当期値 |
| `observed_value` | FLOAT64 | Trigger operatorへ渡す値。既存DuckDBの入力契約と同義 |
| `reference_available_at` / `current_available_at` | TIMESTAMP | 判断時点で利用可能だった日時 |
| `comparison_status` | STRING | `ok`または欠損・非対応理由 |
| `reporting_scope` / `accounting_standard` | STRING | 連結区分と会計基準 |
| `current_disclosure_number` / `reference_disclosure_number` | STRING | 比較に使った開示番号 |
| `ingestion_run_id`、`raw_uri`、hash列 | 各型 | 元responseまで辿るlineage |

`forecast_variance`は、前期の最初の通期開示で公表された翌期会社予想と、当期の最初の
通期実績を比較します。四半期中に修正された直前予想との差は同じ意味ではないため、現時点では
混ぜません。

Operational Trigger評価では、FastAPIが対象銘柄・指標・比較種別・評価基準日でこのFactを絞り、
Ruleの演算子・閾値を適用します。EvaluationはPostgreSQLへimmutableに保存され、以後のWeb閲覧は
BigQueryを再queryしません。`comparison_key + current/reference disclosure number`をevent keyへ含め、
同じRule version・mode・Factの再評価を重複保存しません。

2026-10-13の初回`dbt build`では135,700行を生成し、source必須項目、一意性、列挙値、
参照日時、比較基準の11テストを含む全16 nodeが成功しました。

同日のローカルParquetとの照合では、既存DuckDB定義134,148行のうち、同じ入力を持つ
132,963行で算出値・状態が一致し、計算式の不一致は0件でした。残る1,185行は、BigQueryの
履歴backfillがローカルParquetより広いこと、およびREIT等の会計基準・連結区分が後から
正規化されたことによる入力snapshot差です。照合は`analytics/dbt/reconcile_duckdb.py`で
再実行できます。

全銘柄を網羅するBigQuery側のsecurity masterはまだロードしていないため、マスタ参照整合性は
未実施です。EDINET DB Source Qualificationの20社だけを正本のように流用せず、J-Quants銘柄
マスタを同じlineage契約でロードした後にdbt relationship testを追加します。
