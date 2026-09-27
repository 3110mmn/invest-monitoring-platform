# 開発ガイド

このプロジェクトを手元で動かし、変更を加えるための手順をまとめます。
プロダクトの目的と全体設計は [README](../README.md) を参照してください。

---

## セットアップ

### 初期設定

```bash
# リポジトリのクローン
git clone https://github.com/saito-structural-data/invest-monitoring-db.git
cd invest-monitoring-db

# バックエンド
python -m venv venv
venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env       # 必要に応じて編集
make db-up
make db-migrate

# テストを実行する場合
venv/bin/pip install -r backend/requirements-dev.txt
make db-test-create      # テスト専用DBを作る（初回のみ）
venv/bin/pytest

# フロントエンド
cd frontend
npm ci
```

### 起動

```bash
# バックエンド (別ターミナル)
cd backend
../venv/bin/uvicorn app.main:app --reload

# フロントエンド (別ターミナル)
cd frontend
npm run dev
```

フロントエンドは読み取り専用モードでないため、テーマ・銘柄・構成銘柄の編集UIがそのまま使えます。

### 接続先を選ぶ

`backend/.env`の`DATABASE_URL`で決まります。接続URLは`provision_roles.py`が生成した
`backend/.env.neon`（Git管理外）にあります。

| 用途 | 使うURL | ロール |
|---|---|---|
| 開発DB | `postgresql://invest:invest@localhost:5432/invest` | 開発用 |
| 実データを編集 | `DATABASE_WRITER_URL` | `app_writer` |
| migration・復旧 | `DATABASE_OWNER_URL` | Owner |

**実データDBへ繋ぐときも`app_writer`を使い、Owner接続はAlembicと復旧のときだけにします。**
`app_writer`ではCRUDはできてもスキーマは変更できず、`CREATE TABLE`・`DROP TABLE`・`TRUNCATE`は
すべて`permission denied`で止まります。

一時的に切り替えるだけなら環境変数でも渡せます。

```bash
cd backend
DATABASE_URL="postgresql://invest:invest@localhost:5432/invest" \
  ../venv/bin/uvicorn app.main:app --reload
```

どちらに繋いでいるかは**画面上部のバナー**が常時表示します。実データDBのときだけ赤く警告します。

- Backend: http://localhost:8000
- Frontend: http://localhost:3000
- API Docs: http://localhost:8000/docs

### データ投入

```bash
# 過去5年分のバックフィル（初回のみ）
cd backend
python scripts/jquants_sync.py prices --years 5

# 日次更新（手動）
python scripts/daily_update.py
```

> 日次更新は GitHub Actions で毎日 UTC 21:00（JST 06:00）に自動実行されます。

### 全銘柄の価格をアーカイブする

分析用に市場全体の四本値を蓄積します。**rawへ保存するだけで、PostgreSQLへはロードしません。**
PostgreSQLは監視対象を管理する役割で、全市場の履歴は分析側の責務だからです。

```bash
cd backend
python scripts/jquants_sync.py archive-prices --date 2026-07-01     # 単日
python scripts/jquants_sync.py archive-prices --all                 # プランが提供する全期間
python scripts/jquants_sync.py archive-prices --catch-up            # 前回の続きから（日次用）

python scripts/jquants_sync.py archive-financials --all             # 財務も同じ形
python scripts/jquants_sync.py archive-financials --catch-up
```

価格と財務で日付の決め方は共通です。片方だけ窓の扱いが違うと取りこぼしの原因になります。

日次ワークフローは `--catch-up` を使います。「取得可能な最新日だけ」を取ると、実行が失敗した
日が穴として残ります。**前回アーカイブ済みの翌日から追いつく**ことで、次回実行で自動的に
埋まります。一度に取りすぎないよう `--max-days`（既定30）で上限を設けています。

取得単位は日付で、**1リクエストで全銘柄（約4,400件）**が返ります。銘柄単位で回すと
リクエスト数が銘柄数に比例しますが、日付単位なら日数にしか比例しません。

契約プランの提供期間外は指定しても拒否されるため、期間は窓へ自動で丸めます。
Freeプランは直近12週を提供しないので、アーカイブは常にその分だけ遅れます。

### 分析用Parquetを組み立てる

rawから分析層のParquetを作ります。**rawが原本でParquetは派生**なので、いつでも作り直せます。

```bash
cd backend
python scripts/build_parquet.py --out ../data/parquet                      # 価格
python scripts/build_financial_parquet.py --out ../data/parquet            # 財務
python scripts/build_parquet.py --out ../data/parquet --publish gs://<bucket>/lake
python scripts/reconcile_parquet.py --parquet ../data/parquet
```

財務は価格と違い、同じ銘柄・同じ期に複数の開示があります（訂正、予想修正）。**開示番号が
別なら別レコードとして残し**、どれが最新かは利用側が決めます。同じ開示日を複数回
アーカイブしたぶんだけを畳みます。

rawの所在は`ingestion_run.raw_path`から引きます。GCSへ公開済みでも手元に実体があれば
そちらを読むため、再構築は数十秒で済みます。手元に無ければGCSから読むので、CIでも動きます。

#### 日次は差分更新にする

```bash
python scripts/build_parquet.py --base gs://<bucket>/lake --publish gs://<bucket>/lake
python scripts/build_financial_parquet.py --base gs://<bucket>/lake --publish gs://<bucket>/lake
```

`--base` は公開済みParquetを土台にし、その`ingestion_run_id`より後のrawだけを読みます。
**全再構築はCIでは使えません。** 2年分のrawは価格490・財務487ファイルあり、使い捨ての
ランナーにはローカルキャッシュが無いため、GCSから1つずつ読むと30分を超えます。
差分なら価格76秒・財務8秒です。

基準線に`ingestion_run_id`を使うのは、単調増加で、rawとParquetの両方に入っているためです。
日付を基準にすると、遡って取り込んだ過去分を取りこぼします。

新しいrawが無い日（休場日など）は何もせず終わります。失敗ではありません。
差分と全再構築の結果が一致することは実測で確認しています（215万行、双方向の差分0件）。

`reconcile_parquet.py` は構造の検査（重複キー、OHLCの大小関係、負値）で異常終了し、
PostgreSQLとの値の比較は乖離の分布を報告するだけで合否にしません。取得元が違うため、
一致を条件にすると実態を隠すことになります。

### Derivedを計算する

`backend/analytics/derived/*.sql` が計算定義で、**この定義が正本です。** 結果は保存せず
都度計算します。materializeするのは、高コスト・複数用途で共有・過去に提示した判断の
Evidence、のいずれかが成立したときだけです。

```bash
cd backend
python scripts/derived.py                                  # 定義の一覧
python scripts/derived.py daily_return --target 7203.T     # 計算して表示
```

DuckDBはin-memoryで使い、`.duckdb`ファイルを作りません。データはParquetにあり、DuckDBは
計算エンジンです。ファイルを作ると、それが第2の正本に見えてしまいます。

#### GCS上のParquetを直接読む

`--parquet-glob` に `gs://` を渡すと、手元にコピーせずGCSのまま計算します。

```bash
gcloud auth application-default login   # 初回のみ。gcloud auth login とは別物
python scripts/derived.py daily_return \
  --parquet-glob 'gs://<bucket>/lake/observed/market_price/**/*.parquet'
```

DuckDB本体のhttpfsはGCSをS3互換として扱うためHMACキーを要求しますが、`runner.connect()` は
fsspec経由でgcsfsへ委譲するので、**鍵の発行は不要**でADCがそのまま効きます。Cloud Runでは
メタデータサーバから資格情報を取るため、この初回ログインも要りません。

日次ワークフローは価格の更新に加えて、プランで取得できる最新の開示日の財務サマリーを取り込みます。
財務の取得に失敗しても価格の更新と公開は止めません。初回投入や銘柄を追加したときは、
銘柄単位で全開示を取得するオプションつきで手動実行します。

**日次ETLのワークフロー定義はこのリポジトリに含めていません。** 実行ログに銘柄名と終値が
出るため、非公開のリポジトリで実行しています。ETLの実装自体は `backend/app/etl/` と
`backend/scripts/` にあり、上記のコマンドから手元でも実行できます。

### 環境変数 (`backend/.env`)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `DATABASE_URL` | PostgreSQL接続URL。Secretとして管理する | `postgresql://invest:invest@localhost:5432/invest` |
| `LEGACY_SQLITE_PATH` | 一度限りのSQLite移行元 | `data/invest.db` |
| `DATABASE_READ_ONLY` | PostgreSQLセッションとHTTP APIの書き込みを無効化する。公開環境では `true` | `false` |
| `BACKFILL_YEARS` | バックフィル期間（年数） | `5` |
| `CORS_ORIGINS` | 許可するオリジン | `["http://localhost:3000"]` |
| `JQUANTS_API_KEY` | J-Quants V2 APIキー | 未設定 |
| `JQUANTS_DAILY_ENABLED` | 日次更新でJ-Quantsを使うか。`false` の間は `jpx_code` の対応があってもyfinanceで取得する | `false` |
| `JQUANTS_BASE_URL` | J-Quants V2 APIベースURL | `https://api.jquants.com/v2` |
| `JQUANTS_TIMEOUT_SECONDS` | J-Quantsリクエストのタイムアウト秒数 | `30` |
| `JQUANTS_REQUESTS_PER_MINUTE` | プランのレート制限（回/分）。この間隔で送信を自動調整する。Free=5 / Light=60 / Standard=120 / Premium=500 | `5` |
| `PARQUET_LAKE` | 分析層のルート（例 `gs://bucket/lake`）。配下の `observed/market_price` と `observed/financial_summary` を読む。**価格と財務はここからしか読まない**ため、未設定だと両方のAPIが503になる。公開デモはデモ用バケットを指す | 未設定 |
| `RAW_DATA_PATH` | 再加工用rawレスポンスの保存先 | `data/raw` |
| `MANAGEMENT_API_ENABLED` | 管理APIを有効化し、変更操作へ管理キーを要求するか | `false` |
| `MANAGEMENT_API_KEY` | 変更操作とデータ管理APIの `X-Admin-Key` 共有キー | 未設定 |

### Dockerでバックエンドを起動

イメージにはDBや認証情報を含めず、実行時に環境変数から渡します。

```bash
make docker-build
make docker-run

# 別ターミナルから確認
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

ローカルコンテナは`host.docker.internal`経由でComposeのPostgreSQLへ接続します。

## ローカルDBと旧SQLite移行

ローカル開発DBはDocker ComposeのPostgreSQLです。本番・公開デモとは自動同期しません。

旧SQLiteの既存データを移すのは初回だけです。`db-import` は移行先を置換するため、事前に対象URLを
確認してください。

```bash
make db-import
```

## 公開デモのデータを作る

`seed_demo.py` がsyntheticデータを生成します。実データは1件も置きません。J-Quants APIは
取得データの第三者提供を禁じているためです。

出力先は2つに分かれます。**どちらも本番と同じ経路で読まれます。**

| 出力先 | 内容 |
|---|---|
| デモPostgreSQL | 戦略・テーマ・監視対象・構成・取込の来歴（Control Plane） |
| デモGCS Parquet | 価格・財務（Data Plane） |

```bash
read -rs 'DEMO_URL?demo DB URL: '       # 入力は履歴に残らない
python backend/scripts/seed_demo.py \
  --database-url "$DEMO_URL" --replace \
  --publish gs://invest-demo-lake/lake
unset DEMO_URL
```

接続先はownerロールが必要です。migrationとTRUNCATEを行うため、`app_reader`では実行できません。
投入先のDB名は許可リストと照合し、一致しなければ**migrationより前に**中断します。
この確認は`--force`でも越えられません。

**`--replace`と`--publish`はセットで実行します。** Parquetの各行は`ingestion_run_id`で
デモPostgreSQLの`ingestion_run`を指しています。PostgreSQLだけ作り直すとidがずれ、
来歴の参照が古い行を指します。

公開先は`gs://invest-demo-lake`配下に限ります。実データのlakeへ書こうとすると拒否します。
公開APIのサービスアカウントは実データのバケットに権限を持たないので、読む側も構造的に
分離されています。

---

## CI と手元での検証

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) がPRとmainへのpushで次を実行します。
手元でも同じコマンドをリポジトリルートから実行できます。

```bash
# バックエンド
ruff check backend                                      # Lint
mypy                                                    # 型チェック
pytest                                                  # テスト
python backend/scripts/generate_schema_docs.py --check  # スキーマとドキュメントの整合
python backend/scripts/check_docs.py                     # リンク切れ・古い用語の検出

# フロントエンド
cd frontend && npm run lint && npx tsc --noEmit && npm test && npm run build
```

テストは全テーブルをTRUNCATEするため、開発DB(`invest`)ではなく専用の `invest_test` に対して実行します。接続先が違う場合はテストが実行されず中断します。

ruffとmypyの設定は [`pyproject.toml`](../pyproject.toml) に集約し、採用するルールを明示しています。
ツールのバージョンが上がってもCIの判定が勝手に変わらないようにするためです。

---

## スキーマ文書の生成

型・NULL可否・キー・制約・インデックスは [構造リファレンス](data/schema-reference.md) にAlembic初期migrationから自動生成しています。スキーマを変更したら次を実行してください。

```bash
cd backend
python scripts/generate_schema_docs.py           # リファレンスとER図を再生成
python scripts/generate_schema_docs.py --check   # 生成物とデータ定義書のずれを検査
```

同じ検査を `backend/tests/test_schema_docs.py` がテストとして実行するため、ドキュメントの更新漏れは `pytest` で失敗します。

なおテストからアプリケーションコードを参照する際は、[`pytest.ini`](../pytest.ini) の
`pythonpath = backend` により `from app...` / `from scripts...` の形で import します。
同じパス解決をエディタへ伝えるため、[`pyrightconfig.json`](../pyrightconfig.json) に
`extraPaths` を設定しています。

---

## API仕様

稼働中のFastAPIが生成する`/docs`（OpenAPI）を正本とします。ローカルでは
http://localhost:8000/docs から確認できます。

---
