# Trigger Operations Pilot

目的は、ユーザーが採用した継続監視条件について、Rule設定→判定→対応を一巡させることです。
個別条件の投資収益への有効性は探索側で検証し、条件ごとにAPIや画面を追加しません。

## 現在の対応範囲

- 適用scope: Investment Target / Mandate / Theme / Watchlist
- 評価単位: scopeの構成Investment Target、通期決算
- 指標: 売上高、営業利益、純利益
- 比較: 期初予想比、前年比、絶対値、黒字・赤字の状態変化
- 演算子: 未満、以下、超、以上、黒字から赤字、赤字から黒字
- 結果: `fired` / `not_fired` / `unavailable`

計算定義の運用正本はBigQuery/dbtの`invest_marts.fct_metric_comparison`です。FastAPIは定義済みFactと
Ruleを照合するだけです。PostgreSQLにはRuleの安定IDと追記型version、判定Evidence、発火後の対応を保存します。
`BIGQUERY_PROJECT`が未設定のローカル開発・fixtureでは、照合済みの
`backend/analytics/derived/metric_comparison_input.sql`をDuckDBで読みます。

## 操作の流れ

Rule（適用scope・指標・比較・演算子・閾値・発火時方針・採用理由）を`/triggers`で登録し、
評価して、発火案件へ対応方針と理由を記録します。条件を変えるときは同じRuleへ新versionを
追加し、過去の評価は書き換えません。一時的に使わないRuleは停止でき、不要と確定したものは
停止後に関連履歴ごと完全削除できます。

画面からの評価は実運用（当日）だけです。閾値の校正に使う過去再生は、DBへ書かない
`backend/scripts/replay_trigger.py`で確かめます。起動に必要な設定は
[開発手順](development.md)を参照してください。

## 判定契約

- FYの同じ会計基準・連結区分だけを比較し、同一期の最初の開示を固定する。
- 期初予想比は、前期FYの最初の開示に含まれる翌期予想を翌期最初のFY実績と比較する。
- 前年比は前年実績、絶対値は当年実績、符号転換は前年実績と当年実績を使う。
- 予想比・前年比の参照値がゼロ以下の場合は率を定義せず`unavailable`とする。
- 開示翌暦日から利用可能とし、欠損・比較不能を`not_fired`へ落とさない。
- 同じRule version・mode・イベントは一度だけ保存する。
- 過去再生は当時の実判断ではない。実運用と混ぜない。
- Review Triggerは人間への見直し要求である。事前にAction Policyまで採用したDecision Triggerは、発火時に
  BUY / SELL / HOLD等の方針を成立させられるが、注文・約定Actionとは分離する。
- Mandate / Theme / Watchlist scopeは評価時に構成Targetへ展開し、実際に判定したTargetをEvidenceへ固定する。
- Rule versionは`on_fire_decision`を持つ。評価時に同じ値をTrigger Instanceへ固定し、後からRuleを
  改訂しても当時の方針を再現できるようにする。MVPではすべて人間の確認を必須とする。
- 発火後のResponseは方針の`accepted` / `overridden` / `deferred`を記録する。上書き時だけ代替方針を必須とする。
  `deferred`は次回確認日を必須とする。無いと要対応の条件（`next_review_on <= CURRENT_DATE`）に
  永久に合致せず、作業キューから消える。
- **判定一覧は履歴ではなく作業キューである。** 既定は実運用（`live`）の要対応案件だけを、開示日の
  降順で返す。要対応は「一度も確認していない」か「再確認日が到来した保留」を指す。過去再生は
  Ruleの校正用であり、人間が対応すべき案件ではないため既定では返さない。
- 停止は`is_active`で表し、一覧から外すのは表示の都合である。停止は終了ではなく中断として扱い、
  再開してもversionは進まない。
- 完全削除は停止中Ruleだけに許可し、Ruleだけを孤立して消さず、version、condition、assignment、
  evaluation、responseを同一トランザクションで削除する。画面には削除対象件数と復元不能であることを表示する。

## 詳細分析との境界

**この画面は「何が発火し、どう対応したか」だけを扱います。** 「なぜ発火したか」は
発火案件を起点に、BigQuery / DuckDB / Notebookで調べます。利益率・CF分解、業種比較、
原因仮説の検証に専用の画面やAPIを追加しません。分析手法が増えるたびにスキーマが
増殖するためです。

調べた結果はWebへ戻しますが、残すのは分析そのものではなく**結論と成果物への参照**です
（分析種別、要約、成果物URI、実施時刻、実施者、対応方針）。複数の発火案件で繰り返し
使われた分析だけを、将来Operational側へ昇格させます。

## 後続

BigQuery＋dbt版は`analytics/dbt/`の`fct_metric_comparison`として実装済みです。
`analytics/dbt/reconcile_duckdb.py`でDuckDB定義との一致を確認します。FastAPIはRuleを手動評価する
時点で対象銘柄・指標・比較種別・基準日をparameterized queryで絞り、1 queryあたりの
`maximum_bytes_billed`を適用します。Web画面の閲覧自体はPostgreSQLだけを読み、BigQueryへ接続しません。

Airflowのローカル縦スライスを導入済みです。取得、raw公開、BigQueryロード、dbt品質検証、
Trigger評価を`financial_disclosure_trigger_pipeline`で接続しています。本番schedulerは未デプロイで、
起動・再実行方法は`orchestration/airflow/README.md`を参照してください。
Rule・Responseは引き続きPostgreSQLの業務状態です。

Trigger Rule自体の有用性や誤発火を評価するRule監査は、Outcomeと運用履歴が蓄積してから
別の責務として設計します。`trigger_response`に監査結果は保存しません。

## 小売20社Themeの準備

EDINET DB Source Qualificationの対象20社は、CSVの社名ではなくJPXコードを全銘柄Parquetへ
照合してThemeへ登録する。Investment Targetは必要な銘柄だけPostgreSQLへ遅延登録され、
Watchlistには追加されない。

```bash
cd backend
../venv/bin/python -m scripts.import_theme_members \
  --theme-key retail-edinet-pilot \
  --theme-name "EDINET DB 小売Pilot" \
  --description "JPX小売業のうちEDINET DB Source Qualification済み20社" \
  --csv ../取り込みデータ/Master/ThemeMembership/retail-edinet-pilot.csv \
  --industry 小売業 \
  --mode append \
  --lake ../data/parquet \
  --dry-run
```

確認後に`--dry-run`を外す。同じコマンドを再実行しても、Theme・Investment Target・現在所属は
重複しない。`append`は追加だけを行う。CSVを特定時点の全構成として同期する場合は、意図しない
手動Membershipの除外を避けるため、明示的に次を指定する。

```bash
  --mode replace --snapshot-at 2026-10-19
```

`replace`はCSVにない現在Membershipの`effective_to`を`snapshot_at`で閉じ、CSV原本の参照先・
SHA-256・追加／除外／継続件数を`theme_membership_import`へ保存する。
