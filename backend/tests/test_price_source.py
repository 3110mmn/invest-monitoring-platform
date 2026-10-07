"""価格の読み出し元を切り替えても、APIの契約が変わらないことを検証する。

PostgreSQL経路は `test_api.py` が押さえている。ここは分析層（Parquet）経路が
同じ形・同じ並びを返すかを見る。片方だけ壊れると、公開環境と管理環境で画面の
挙動が食い違う。
"""

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import analytics_connection
from app.config import settings
from app.repositories.price_source import ParquetPriceSource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_parquet import PRICE_SCHEMA


def _row(days_ago: int, close: float, *, code="72030", key="7203.T", source="jquants"):
    """観測1行。日付は今日からの相対で置き、`days` の絞り込みを試せるようにする。"""
    return {
        "target_key": key,
        "jpx_code": code,
        "source_key": source,
        "obs_date": date.today() - timedelta(days=days_ago),
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 1500.0,
        "price_basis": "adjusted",
        "ingestion_run_id": 7,
        "built_at": datetime(2026, 7, 1, tzinfo=UTC),
    }


@pytest.fixture
def parquet_source(tmp_path):
    """Parquetを書いて、そこを読むPriceSourceを返す。"""

    def _build(rows):
        target = tmp_path / "observed" / "market_price" / "year=2026"
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(rows, schema=PRICE_SCHEMA), target / "part-0.parquet"
        )
        glob = str(tmp_path / "observed" / "market_price" / "**" / "*.parquet")
        from analytics.runner import connect

        return ParquetPriceSource(connect(glob))

    return _build


TOYOTA = {"target_id": 1, "target_key": "7203.T", "target_name": "トヨタ"}


def test_price_history_is_ascending_by_obs_date(parquet_source):
    """並び順はAPIの契約。画面は先頭を期間の開始日として扱う。"""
    source = parquet_source([_row(0, 300.0), _row(2, 100.0), _row(1, 200.0)])

    rows = source.price_history(TOYOTA, days=30)

    assert [r["close_price"] for r in rows] == [100.0, 200.0, 300.0]


def test_price_history_carries_the_control_plane_target_id(parquet_source):
    """Parquetに無い `target_id` はControl Plane側から埋める。

    分析層はbusiness keyしか持たない。APIの応答形はPostgreSQL経路と揃える必要がある。
    """
    source = parquet_source([_row(0, 300.0)])

    rows = source.price_history(TOYOTA, days=30)

    assert rows[0]["target_id"] == 1
    assert rows[0]["ingestion_run_id"] == 7
    assert rows[0]["volume"] == 1500.0


def test_days_window_excludes_older_observations(parquet_source):
    source = parquet_source([_row(0, 300.0), _row(100, 100.0)])

    rows = source.price_history(TOYOTA, days=30)

    assert len(rows) == 1


def test_price_history_includes_daily_and_period_cumulative_returns(parquet_source):
    source = parquet_source([_row(2, 100.0), _row(1, 110.0), _row(0, 99.0)])

    rows = source.price_history(TOYOTA, days=30)

    assert rows[0]["daily_return"] is None
    assert rows[0]["cumulative_return"] == pytest.approx(0.0)
    assert rows[1]["daily_return"] == pytest.approx(0.1)
    assert rows[1]["cumulative_return"] == pytest.approx(0.1)
    assert rows[2]["daily_return"] == pytest.approx(-0.1)
    assert rows[2]["cumulative_return"] == pytest.approx(-0.01)


def test_jquants_is_preferred_over_yfinance_on_the_same_day(parquet_source):
    """採用の規則は `preferred_price` に一本化されている。画面とDerivedで食い違わせない。"""
    source = parquet_source(
        [_row(0, 300.0, source="jquants"), _row(0, 999.0, source="yfinance")]
    )

    rows = source.price_history(TOYOTA, days=30)

    assert len(rows) == 1
    assert rows[0]["close_price"] == 300.0


def test_target_absent_from_the_analytics_layer_returns_nothing(parquet_source):
    """分析層に無い銘柄は空で返す。別銘柄を混ぜない。"""
    source = parquet_source([_row(0, 300.0)])

    rows = source.price_history(
        {"target_id": 9, "target_key": "SPY", "target_name": "S&P500 ETF"}, days=30
    )

    assert rows == []


def test_synthetic_keys_work_without_a_jpx_code(parquet_source):
    """デモデータは日本の証券コードを持たない。公開デモも同じ経路を通すため必須。

    `security_key` が `target_key` へ退避するので、jpx_codeがNULLでも銘柄が畳まれない。
    """
    source = parquet_source(
        [
            _row(0, 100.0, code=None, key="ALPHA.DEMO"),
            _row(0, 200.0, code=None, key="BRAVO.DEMO"),
        ]
    )
    alpha = {"target_id": 1, "target_key": "ALPHA.DEMO", "target_name": "Alpha"}
    bravo = {"target_id": 2, "target_key": "BRAVO.DEMO", "target_name": "Bravo"}

    assert source.price_history(alpha, days=30)[0]["close_price"] == 100.0
    assert len(source.latest_prices([alpha, bravo])) == 2, (
        "jpx_codeがNULL同士でも別銘柄として扱う"
    )


def test_latest_prices_returns_one_row_per_target(parquet_source):
    source = parquet_source(
        [
            _row(0, 300.0),
            _row(1, 200.0),
            _row(0, 50.0, code="67580", key="6758.T"),
        ]
    )
    sony = {"target_id": 2, "target_key": "6758.T", "target_name": "ソニー"}

    rows = source.latest_prices([TOYOTA, sony])

    assert len(rows) == 2
    by_key = {row["target_key"]: row for row in rows}
    assert by_key["7203.T"]["close_price"] == 300.0
    assert by_key["7203.T"]["latest_date"] == date.today()
    assert by_key["6758.T"]["target_id"] == 2


def test_latest_prices_omits_targets_without_observations(parquet_source):
    """価格が無い銘柄は行を作らない。欠損をゼロで埋めない方針と揃える。"""
    source = parquet_source([_row(0, 300.0)])
    unknown = {"target_id": 3, "target_key": "9999.T", "target_name": "未取得"}

    rows = source.latest_prices([TOYOTA, unknown])

    assert [row["target_key"] for row in rows] == ["7203.T"]


def test_latest_prices_without_targets_does_not_query(parquet_source):
    source = parquet_source([_row(0, 300.0)])

    assert source.latest_prices([]) == []


def test_analytics_connection_is_absent_when_no_glob_is_configured(monkeypatch):
    """所在が未設定なら分析層へ繋がない。公開APIはこの状態で動く。"""
    monkeypatch.setattr(settings, "parquet_lake", None)

    assert analytics_connection.open_analytics_connection() is None
    assert analytics_connection.get_analytics_connection() is None


def test_api_serves_parquet_prices_in_the_same_shape(client, db, parquet_source, monkeypatch):
    """分析層を読む配備でも、エンドポイントの応答形が変わらないこと。

    レスポンスモデルがParquetに無い列を必須にしていると、ここで422や500になる。
    PostgreSQL経路と同じ画面が動く保証をAPIの外側から取る。
    """
    target_id = db.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type) "
        "VALUES ('7203.T', 'トヨタ', 'individual_stock')"
    ).lastrowid
    db.execute(
        "INSERT INTO watchlist_entry (target_id, status) VALUES (?, 'monitoring')",
        (target_id,),
    )
    db.commit()
    source = parquet_source([_row(0, 300.0), _row(1, 200.0)])
    monkeypatch.setattr(
        "app.routers.domain.investment_targets.get_analytics_connection",
        lambda: source.conn,
    )

    history = client.get(f"/api/investment-targets/{target_id}/prices?days=30")
    latest = client.get("/api/investment-targets/latest-prices")

    assert history.status_code == 200, history.text
    assert [row["close_price"] for row in history.json()] == [200.0, 300.0]
    assert history.json()[0]["target_id"] == target_id
    assert history.json()[0]["source_key"] == "jquants"
    assert history.json()[0]["cumulative_return"] == pytest.approx(0.0)
    assert history.json()[1]["daily_return"] == pytest.approx(0.5)
    assert latest.status_code == 200, latest.text
    assert latest.json()[0]["latest_date"] == date.today().isoformat()


def test_api_still_reports_missing_target_from_the_control_plane(client, parquet_source, monkeypatch):
    """銘柄の実在はPostgreSQLが答える。分析層に価格があっても管理外なら404。"""
    source = parquet_source([_row(0, 300.0)])
    monkeypatch.setattr(
        "app.routers.domain.investment_targets.get_analytics_connection",
        lambda: source.conn,
    )

    assert client.get("/api/investment-targets/999999/prices").status_code == 404


def test_views_work_from_another_session(parquet_source):
    """別sessionからviewを開いても実体が残ること。

    所在を `SET VARIABLE` で束縛したまま view を定義すると、catalogは共有される一方
    session変数は引き継がれず、実体が `read_parquet(NULL)` になる。DuckDBのUIも
    `cursor()` も別sessionなので、これを踏むと「Summary unavailable」とだけ表示され
    理由が分からない。所在はviewの定義へリテラルで埋めてある。
    """
    source = parquet_source([_row(0, 300.0)])

    assert source.conn.execute("SELECT COUNT(*) FROM preferred_price").fetchone()[0] == 1
    assert source.conn.cursor().execute(
        "SELECT COUNT(*) FROM preferred_price"
    ).fetchone()[0] == 1


def test_app_starts_even_when_the_analytics_layer_is_unreachable(monkeypatch):
    """分析層へ繋がらなくてもアプリは起動する。

    価格が出ないだけの障害を、全機能の停止に化けさせない。Parquetがまだ無い状態での
    初回デプロイもここに当たる。
    """
    monkeypatch.setattr(settings, "parquet_lake", "/nonexistent-lake")

    assert analytics_connection.open_analytics_connection() is None


def test_price_endpoints_report_503_when_the_analytics_layer_is_missing(client, db, monkeypatch):
    """価格が読めないことを200の空配列で隠さない。設定漏れと在庫なしを区別する。"""
    target_id = db.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type) "
        "VALUES ('7203.T', 'トヨタ', 'individual_stock')"
    ).lastrowid
    db.commit()
    monkeypatch.setattr(
        "app.routers.domain.investment_targets.get_analytics_connection", lambda: None
    )

    assert client.get(f"/api/investment-targets/{target_id}/prices").status_code == 503
    assert client.get("/api/investment-targets/latest-prices").status_code == 503


def test_readiness_reports_the_analytics_layer(client, monkeypatch):
    """価格は分析層からしか読まないので、PostgreSQLが健全でもreadyとは言えない。"""
    monkeypatch.setattr("app.main.get_analytics_connection", lambda: None)

    body = client.get("/ready").json()

    assert body["analytics"] == "unavailable"
    assert body["status"] == "degraded"
