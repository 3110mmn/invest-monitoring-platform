# Financial comparison dbt project

BigQueryのJ-Quants財務開示履歴から、予想比・前年比・絶対値・符号変化を
同じ入力契約で評価できる`fct_metric_comparison`を構築します。

dbtはBackendと依存関係が競合するため、専用の仮想環境で実行します。

```bash
# dbt 1.10のためPython 3.13以下を使う（Python 3.14は未対応）。
python3.13 -m venv .venv-dbt
source .venv-dbt/bin/activate
python -m pip install -r backend/requirements-dbt.txt
dbt build --project-dir analytics/dbt --profiles-dir analytics/dbt
```

認証はApplication Default Credentialsを使います。接続先は環境変数で変更できます。

```bash
export BIGQUERY_PROJECT=invest-dwh
export BIGQUERY_LOCATION=australia-southeast1
```

各Queryには`maximum_bytes_billed=10 GiB`を設定しています。実行時に上限を超える
スキャンが必要な場合、課金を開始せず失敗します。

## モデル

- `stg_financial_disclosures`: J-Quants開示履歴の列と銘柄キーを統一
- `int_actual_metric_values`: 最初の通期開示を指標別の実績へ縦持ち化
- `int_forecast_metric_values`: 前期通期開示時点の翌期会社予想を縦持ち化
- `int_comparison_reference`: 実績と、当時利用可能だった予想・前年実績を対応付け
- `fct_metric_comparison`: Ruleから再利用する比較Fact

`forecast_variance`は既存DuckDB定義と同じく、前期の最初の通期開示に掲載された
翌期会社予想と当期の最初の通期実績を比較します。四半期の修正予想を使う
「直前予想比」は意味が異なるため、同じ比較種別へ混ぜません。

`analyses/`には、営業利益予実差Triggerの閾値・判定可能性・翌期データ接続数を
確認する探索SQLを置いています。これはRuleを作成する処理ではなく、Ruleへ昇格させる前の
校正用です。実測結果と採用判断は
`memo/測定結果/2026-10-14_営業利益予実差Trigger校正.md`に残します。

`retail_operating_income_trigger_cases.sql`は、その発火銘柄とEDINET DB小売Pilotを突合し、
売上・利益率・CFをケース分析します。EDINET有報の公表日時を露出し、Trigger発火後に公表された
値を発火時点のEvidenceとして誤用しない設計です。

ローカルParquetとBigQueryの重複範囲は、次のコマンドで既存DuckDB定義との一致を
検査できます。

```bash
python analytics/dbt/reconcile_duckdb.py
```

Trigger閾値まで照合する場合は、対象を限定して実行します。

```bash
python analytics/dbt/reconcile_duckdb.py \
  --metric operating_income \
  --comparison forecast_variance \
  --threshold=-20 \
  --boundary-width=1
```
