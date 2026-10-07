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

### 環境変数 (`backend/.env`)

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `DATABASE_URL` | PostgreSQL接続URL。Secretとして管理する | `postgresql://invest:invest@localhost:5432/invest` |
| `DATABASE_READ_ONLY` | PostgreSQLセッションとHTTP APIの書き込みを無効化する。通常の公開APIは `true`。期限付きの公開デモ投資枠書き込みを有効にする場合のみ `false` とし、`PUBLIC_DEMO_WRITE_ENABLED=true` およびデモ専用DBロールを併用する | `false` |
| `DATABASE_ENVIRONMENT_OVERRIDE` | DB環境表示を明示する。公開デモでは `demo` | URLから自動判定 |
| `PUBLIC_DEMO_WRITE_ENABLED` | 専用demo DB上で、期限付き投資枠の限定書き込みを許可する | `false` |
| `PUBLIC_DEMO_MANDATE_TTL_HOURS` | 公開デモで作成した投資枠を有効にする時間数 | `1` |
| `PUBLIC_DEMO_MAX_ACTIVE_MANDATES` | 公開デモ内で同時に保持する一時投資枠数の上限 | `20` |
| `PUBLIC_DEMO_MAX_ASSIGNMENTS_PER_MANDATE` | 一時投資枠ごとの投資対象割当数の上限 | `20` |
| `PUBLIC_DEMO_WRITES_PER_MINUTE` | 公開デモ書き込みのプロセス内レート制限（回/分・送信元単位） | `20` |
| `BACKFILL_YEARS` | バックフィル期間（年数） | `5` |
| `CORS_ORIGINS` | 許可するオリジン | `["http://localhost:3000"]` |
| `JQUANTS_API_KEY` | J-Quants V2 APIキー | 未設定 |
| `JQUANTS_BASE_URL` | J-Quants V2 APIベースURL | `https://api.jquants.com/v2` |
| `JQUANTS_TIMEOUT_SECONDS` | J-Quantsリクエストのタイムアウト秒数 | `30` |
| `JQUANTS_REQUESTS_PER_MINUTE` | プランのレート制限（回/分）。この間隔で送信を自動調整する。Free=5 / Light=60 / Standard=120 / Premium=500 | `5` |
| `PARQUET_LAKE` | 分析層のルート（例 `gs://bucket/lake`）。配下の `observed/market_price` と `observed/financial_summary` を読む。**価格と財務はここからしか読まない**ため、未設定だと両方のAPIが503になる。公開デモはデモ用バケットを指す | 未設定 |
| `RAW_DATA_PATH` | 再加工用rawレスポンスの保存先 | `data/raw` |
| `RAW_DATA_URI` | rawを永続化するGCS prefix。**取得を手元で実行するなら設定する。** rawは原本で、Parquetもここから作り直す | 未設定 |
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

## ローカルDB

ローカル開発DBはDocker ComposeのPostgreSQLです。本番・公開デモとは自動同期しません。

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

## データ取得と分析層の操作

J-Quantsからのアーカイブ、Parquetの組み立て、Derivedの計算、公開デモのデータ生成は、
認証情報とクラウド権限を必要とする運用手順です。手順は非公開の運用メモで管理します。

設計上の理由は[アーキテクチャ](architecture.md)と[データ層の論理設計](data/design-principles.md)に
あります。
