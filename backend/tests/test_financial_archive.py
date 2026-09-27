"""財務アーカイブが、rawだけを残しPostgreSQLを汚さないことを確認する。

価格と同じ方針。全市場の履歴はData Plane側の責務で、Serving DBへ溜め込まない。
"""

from pathlib import Path

import pytest

from app.etl.pipeline import JQuantsMarketPipeline


class _StubClient:
    base_url = "https://example.test/v2"

    def __init__(self, by_date: dict[str, list[dict]], failures: set[str] | None = None):
        self._by_date = by_date
        self._failures = failures or set()
        self.calls: list[str] = []

    def financial_summaries(self, *, code=None, date=None):
        assert code is None, "全市場アーカイブは開示日単位で取得する"
        self.calls.append(date)
        if date in self._failures:
            raise RuntimeError("plan window")
        return self._by_date.get(date, [])


def _disclosure(code: str, day: str) -> dict:
    return {
        "Code": code, "DiscNo": f"{code}-{day}", "DiscDate": day, "DiscTime": "15:00",
        "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY", "Sales": "1000",
    }


@pytest.fixture
def pipeline(db, tmp_path):
    client = _StubClient({"2026-05-12": [_disclosure("72030", "2026-05-12")]})
    return JQuantsMarketPipeline(db, client, tmp_path, Path.cwd()), client


def test_archive_saves_raw_without_loading_financials(db, pipeline):
    """rawは残すが、financial_disclosure へは書かない。"""
    market, _ = pipeline

    result = market.archive_financials(["2026-05-12"])

    assert result["fetched"] == 1
    assert db.execute("SELECT COUNT(*) AS n FROM financial_disclosure").fetchone()["n"] == 0


def test_one_request_per_disclosure_date(db, pipeline):
    """取得単位は開示日。銘柄数に比例してリクエストが増えない。"""
    market, client = pipeline

    market.archive_financials(["2026-05-12"])

    assert client.calls == ["2026-05-12"]


def test_each_date_gets_its_own_run(db, tmp_path):
    """1日=1run=1raw。価格と同じ粒度に揃える。"""
    client = _StubClient({
        "2026-05-12": [_disclosure("72030", "2026-05-12")],
        "2026-05-13": [_disclosure("67580", "2026-05-13")],
    })
    market = JQuantsMarketPipeline(db, client, tmp_path, Path.cwd())

    market.archive_financials(["2026-05-12", "2026-05-13"])

    runs = db.execute(
        "SELECT raw_path FROM ingestion_run WHERE job_type = 'jquants_financial_archive_v1'"
    ).fetchall()
    assert len({r["raw_path"] for r in runs}) == 2


def test_day_without_disclosures_is_not_a_failure(db, tmp_path):
    """開示が無い日はAPIが空で返す。取得できなかったのではない。"""
    market = JQuantsMarketPipeline(db, _StubClient({"2026-05-12": []}), tmp_path, Path.cwd())

    result = market.archive_financials(["2026-05-12"])

    assert result["failed"] == 0
    run = db.execute(
        "SELECT status, raw_path FROM ingestion_run ORDER BY ingestion_run_id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "succeeded"
    assert run["raw_path"] is None


def test_failed_date_does_not_stop_the_rest(db, tmp_path):
    """1日失敗しても残りを続ける。プランの窓の外が混ざりうる。"""
    client = _StubClient(
        {"2026-05-12": [_disclosure("72030", "2026-05-12")]}, failures={"2026-05-13"}
    )
    market = JQuantsMarketPipeline(db, client, tmp_path, Path.cwd())

    result = market.archive_financials(["2026-05-12", "2026-05-13"])

    assert result["dates"] == 1
    assert result["failed"] == 1
