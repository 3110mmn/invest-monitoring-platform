"""全銘柄アーカイブが、rawだけを残しPostgreSQLを汚さないことを確認する。

PostgreSQLはControl Plane（何を監視するか）であり、全市場の履歴はData Plane側の責務。
ここでUPSERTまで走ると、配信に不要なデータをServing DBへ溜め込むことになる。
"""

from pathlib import Path

import pytest

from app.etl.fetchers.jquants import JQuantsClient
from app.etl.pipeline import JQuantsMarketPipeline


class _StubClient:
    base_url = "https://example.test/v2"

    def __init__(self, by_date: dict[str, list[dict]], failures: set[str] | None = None):
        self._by_date = by_date
        self._failures = failures or set()
        self.calls: list[str] = []

    def daily_prices(self, *, code=None, date=None, date_from=None, date_to=None):
        assert code is None, "全市場アーカイブは日付単位で取得する"
        self.calls.append(date)
        if date in self._failures:
            raise RuntimeError("plan window")
        return self._by_date.get(date, [])


def _row(code: str, day: str) -> dict:
    return {"Date": day, "Code": code, "O": 1.0, "H": 2.0, "L": 0.5, "C": 1.5, "Vo": 100}


@pytest.fixture
def pipeline(db, tmp_path):
    client = _StubClient({"2026-07-01": [_row("13010", "2026-07-01"), _row("72030", "2026-07-01")]})
    return JQuantsMarketPipeline(db, client, tmp_path, Path.cwd()), client


def test_archive_saves_raw_without_loading_prices(db, pipeline):
    """rawは残すが、market_price_observation へは書かない。"""
    market, _ = pipeline

    result = market.archive_market_prices(["2026-07-01"])

    assert result["fetched"] == 2
    rows = db.execute("SELECT COUNT(*) AS n FROM market_price_observation").fetchone()
    assert rows["n"] == 0


def test_archive_records_the_run_and_raw_path(db, pipeline):
    """来歴を残す。rawの所在が分からなければ保存していないのと同じ。"""
    market, _ = pipeline

    market.archive_market_prices(["2026-07-01"])

    run = db.execute(
        "SELECT status, fetched_count, loaded_count, raw_path FROM ingestion_run ORDER BY ingestion_run_id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "succeeded"
    assert run["loaded_count"] == 0
    assert run["raw_path"] is not None


def test_each_date_gets_its_own_run_and_raw_file(db, tmp_path):
    """1日=1run=1rawにする。まとめると巨大ファイルになり、日次実行とも食い違う。"""
    client = _StubClient({
        "2026-07-01": [_row("13010", "2026-07-01")],
        "2026-07-02": [_row("13010", "2026-07-02")],
    })
    market = JQuantsMarketPipeline(db, client, tmp_path, Path.cwd())

    market.archive_market_prices(["2026-07-01", "2026-07-02"])

    runs = db.execute(
        "SELECT requested_from, raw_path FROM ingestion_run ORDER BY ingestion_run_id"
    ).fetchall()
    assert len(runs) == 2
    assert len({r["raw_path"] for r in runs}) == 2


def test_market_holiday_is_not_a_failure(db, tmp_path):
    """休場日はAPIが空で返す。取得できなかったのではないので失敗にしない。"""
    client = _StubClient({"2026-07-01": []})
    market = JQuantsMarketPipeline(db, client, tmp_path, Path.cwd())

    result = market.archive_market_prices(["2026-07-01"])

    assert result["failed"] == 0
    run = db.execute("SELECT status, raw_path FROM ingestion_run ORDER BY ingestion_run_id DESC LIMIT 1").fetchone()
    assert run["status"] == "succeeded"
    assert run["raw_path"] is None


def test_one_request_per_date_not_per_symbol(db, pipeline):
    """取得単位は日付。銘柄数に比例してリクエストが増えない。"""
    market, client = pipeline

    market.archive_market_prices(["2026-07-01"])

    assert client.calls == ["2026-07-01"]


def test_failed_date_does_not_stop_the_rest(db, tmp_path):
    """プランの窓の外や休場日が混ざっても、残りの日を続ける。"""
    client = _StubClient(
        {"2026-07-01": [_row("13010", "2026-07-01")]}, failures={"2026-07-02"}
    )
    market = JQuantsMarketPipeline(db, client, tmp_path, Path.cwd())

    result = market.archive_market_prices(["2026-07-01", "2026-07-02"])

    assert result["dates"] == 1
    assert result["failed"] == 1


def test_client_rejects_code_and_date_together():
    """codeとdateは排他。両方指定はAPI仕様上あり得ない。"""
    client = JQuantsClient("key", base_url="https://example.test/v2")

    with pytest.raises(ValueError):
        client.daily_prices(code="72030", date="2026-07-01")

    with pytest.raises(ValueError):
        client.daily_prices()
