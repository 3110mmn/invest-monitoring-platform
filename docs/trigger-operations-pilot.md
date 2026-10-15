# Trigger Operations Pilot

目的は、ユーザーが採用した継続監視条件について、Rule設定→判定→対応を一巡させることです。
個別条件の投資収益への有効性は探索側で検証し、条件ごとにAPIや画面を追加しません。

## 現在の対応範囲

- 対象: Investment Target、通期決算
- 指標: 売上高、営業利益、純利益
- 比較: 期初予想比、前年比、絶対値、黒字・赤字の符号転換
- 演算子: 以上、以下、黒字から赤字、赤字から黒字
- 結果: `fired` / `not_fired` / `unavailable`

計算定義は`backend/analytics/derived/metric_comparison_input.sql`に置き、FastAPIは定義済み結果と
Ruleを照合するだけです。PostgreSQLにはRuleの安定IDと追記型version、判定Evidence、発火後の対応を保存します。

## 使い方

1. migration 0013まで適用し、財務Parquetを含む`PARQUET_LAKE`でAPIを起動する。
2. `/triggers`で投資対象・指標・比較・演算子・閾値・採用理由を保存する。
3. 最初は過去再生で動作を確認する。実運用はRule登録後に公表されたデータだけを対象にする。
4. 発火した評価へ対応方針、理由、必要ならその案件の次回確認日を記録する。
5. 条件を変更するときは同じRuleへ新versionを追加する。過去の評価は書き換えない。

DBへ書かずに確認する例:

```sh
PYTHONPATH=backend venv/bin/python -m scripts.replay_trigger \
  --lake data/parquet --target 2796.T \
  --metric operating_income --comparison forecast_variance \
  --operator lte --threshold -10 --as-of 2026-10-09
```

## 判定契約

- FYの同じ会計基準・連結区分だけを比較し、同一期の最初の開示を固定する。
- 期初予想比は、前期FYの最初の開示に含まれる翌期予想を翌期最初のFY実績と比較する。
- 前年比は前年実績、絶対値は当年実績、符号転換は前年実績と当年実績を使う。
- 予想比・前年比の参照値がゼロ以下の場合は率を定義せず`unavailable`とする。
- 開示翌暦日から利用可能とし、欠損・比較不能を`not_fired`へ落とさない。
- 同じRule version・mode・イベントは一度だけ保存する。
- 過去再生は当時の実判断ではない。実運用と混ぜない。
- Trigger発火は売買指示ではなく、人間への見直し要求である。

## 詳細分析との境界

**この画面は「何が発火し、どう対応したか」だけを扱います。** 「なぜ発火したか」は
発火案件を起点に、BigQuery / DuckDB / Notebookで調べます。利益率・CF分解、業種比較、
原因仮説の検証に専用の画面やAPIを追加しません。分析手法が増えるたびにスキーマが
増殖するためです。

調べた結果はWebへ戻しますが、残すのは分析そのものではなく**結論と成果物への参照**です
（分析種別、要約、成果物URI、実施時刻、実施者、対応方針）。複数の発火案件で繰り返し
使われた分析だけを、将来Operational側へ昇格させます。

## 後続

同じ入力契約のBigQuery＋dbt版は`analytics/dbt/`にあり、`fct_metric_comparison`として
実装済みです。`analytics/dbt/reconcile_duckdb.py`でDuckDB定義との一致を確認します。
FastAPIが読むのは現時点でもDuckDB側で、閲覧ごとのスキャン課金を避けています。

Airflowは未導入です。取得、品質検証、変換、Trigger評価の依存と再実行をGitHub Actionsで
表現しきれなくなった時点で検討します。Rule・Responseは引き続きPostgreSQLの業務状態です。

Trigger Rule自体の有用性や誤発火を評価するRule監査は、Outcomeと運用履歴が蓄積してから
別の責務として設計します。`trigger_response`に監査結果は保存しません。
