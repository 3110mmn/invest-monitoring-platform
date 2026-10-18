# Airflow operational pipeline

`financial_disclosure_trigger_pipeline`は、継続運用へ昇格した財務Triggerだけを日次評価します。
探索分析やEDINET DBの詳細分析は対象外です。

```text
J-Quants財務開示取得
  -> immutable rawをGCSへ公開
  -> 新規rawがある場合だけBigQueryへ冪等ロード
  -> dbt run
  -> dbt test（品質ゲート）
  -> PostgreSQLの有効なTrigger Ruleを評価
  -> ダッシュボード作業キュー件数をログ出力
```

新規rawがなければ`load_bigquery`が終了コード99を返し、後続処理は`skipped`になります。
したがって、平日の空振り確認でdbtとTrigger用BigQuery queryは実行しません。

## ローカル起動

AirflowはBackend/dbtとは別環境に置きます。Airflow 3.3.2のローカル用`standalone`構成であり、
本番運用時はmetadata DBとexecutorを別途選定します。

```bash
gcloud auth application-default login
export GCP_ADC_PATH="$HOME/.config/gcloud/application_default_credentials.json"
set -a; source .env; set +a
make airflow-up
make airflow-logs
```

UIは <http://localhost:8081> です。初回の管理者認証情報はコンテナログへ出ます。

必要な環境変数は`DATABASE_URL`、`JQUANTS_API_KEY`、`RAW_DATA_URI`、
`BIGQUERY_PROJECT`です。GCP credentialはコンテナへ読み取り専用でmountします。

## 手動実行・backfill

通常実行はJ-Quants契約窓の未取得日を`--catch-up`します。手動DAG Runでは次のParamsを指定できます。

- `archive_from` / `archive_to`: 両方指定したときだけ、その期間を取得
- `as_of_date`: Triggerの評価基準日。未指定ならDAGのdata interval終了日（JST）
- `evaluate_existing`: 手動確認用。`true`の場合は新規rawが0件でも、既存の
  BigQueryデータに対してdbt・品質ゲート・Trigger評価を実行する

`evaluate_existing=true`はrawの全件再ロードを行わない。`load_bigquery`は
`loaded_responses=0`で正常終了し、後続だけを通す。通常の定期実行では`false`のままとし、
新規rawがなければ従来どおり後続をskipする。

同じrawはBigQueryの`MERGE`、同じTrigger eventはPostgreSQLの一意制約により重複しません。
`trigger_evaluation.orchestration_run_id`からAirflowのRun IDへ追跡できます。

## 障害時の契約

- raw取得・公開に失敗した場合はロードしない
- dbt testに失敗した場合はTriggerを評価しない
- Airflow UIから失敗taskだけ再実行できる
- dashboard taskは通知そのものではなく、PostgreSQLを読むWeb UIのキュー件数をログへ出す
- 外部通知は通知先とSLOが決まってから後続taskとして追加する
