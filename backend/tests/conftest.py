import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

# Tests must never inherit a production/Neon DATABASE_URL from .env.
# Every test truncates all tables, so the target must be a throwaway test database.
# The default deliberately differs from the development database (`invest`): pointing
# the suite at it would silently destroy local development data on every run.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://invest:invest@localhost:5432/invest_test",
)

from app.database import Connection, connect_database, get_db, get_readiness_db
from app.main import app
from db.postgres_migrations import upgrade_database

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_financial_parquet import build_schema
from build_parquet import PRICE_SCHEMA

# Databases this suite is allowed to truncate. Development (`invest`) and production
# (`neondb`) are excluded on purpose; there is no option to override this.
TEST_DATABASE_NAMES = frozenset({"invest_test"})

_TABLES = (
    "theme_investment_target",
    "investment_target_identifier",
    "ingestion_error",
    "ingestion_run",
    "data_source",
    "investment_target",
    "theme",
    "strategy",
)


def _reset_database(conn: Connection) -> None:
    conn.execute(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
    conn.execute("""
        INSERT INTO strategy (strategy_key, strategy_name, description)
        VALUES
            ('core', 'Core', '中核となる長期保有戦略'),
            ('satellite', 'Satellite', '成長機会を取り込む補完戦略'),
            ('alternatives', 'Alternatives', '伝統資産以外の分散戦略')
    """)
    conn.commit()


@pytest.fixture(scope="session", autouse=True)
def migrated_postgres() -> None:
    # 接続先を確かめてからDDLとTRUNCATEへ進む。名前が違えば何も実行しない。
    conn = connect_database(read_only=False)
    try:
        row = conn.execute("SELECT current_database() AS name").fetchone()
        assert row is not None
        name = str(row["name"])
    finally:
        conn.close()
    if name not in TEST_DATABASE_NAMES:
        pytest.exit(
            f"テストの接続先が `{name}` です。テストは全テーブルをTRUNCATEするため、"
            "専用のテストDBだけを対象にします。\n"
            "許可されている名前: " + ", ".join(sorted(TEST_DATABASE_NAMES)) + "\n"
            "ローカルでは `make db-test-create` で作成できます。",
            returncode=1,
        )
    upgrade_database()


@pytest.fixture
def db(migrated_postgres) -> Iterator[Connection]:
    conn = connect_database(read_only=False)
    _reset_database(conn)
    try:
        yield conn
    finally:
        conn.rollback()
        _reset_database(conn)
        conn.close()


@pytest.fixture(scope="session")
def analytics_lake(tmp_path_factory) -> str:
    """APIテスト用の空のlakeを1度だけ作り、そのルートを返す。

    価格も財務も読み出し経路は分析層の1本だけなので、分析層が無い状態のアプリは
    本番でもデモでも存在しない。テストのアプリも同じ形にする。中身に依存する検証は
    `test_price_source.py` と `test_financial_source.py` が自前のParquetで行う。
    """
    lake = tmp_path_factory.mktemp("lake")
    for subpath, schema in (
        ("observed/market_price", PRICE_SCHEMA),
        ("observed/financial_summary", build_schema()),
    ):
        partition = lake / subpath / "year=2026"
        partition.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist([], schema=schema), partition / "part-0.parquet"
        )
    return str(lake)


@pytest.fixture
def client(db: Connection, analytics_lake: str):
    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_readiness_db] = override_get_db
    # lifespanが分析層へ接続する。所在を渡さないとアプリが起動できない。
    from app.config import settings

    previous = settings.parquet_lake
    settings.parquet_lake = analytics_lake
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.parquet_lake = previous
        app.dependency_overrides.clear()
