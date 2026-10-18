from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.analytics import bigquery_financial_history as subject


class CompletedJob:
    def result(self) -> None:
        return None


class RowJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self.rows


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.query_job_configs: list[Any] = []
        self.loaded_rows: list[dict[str, Any]] = []
        self.destination: str | None = None
        self.deleted: list[str] = []

    def query(self, query: str, *, location: str | None = None, job_config: Any = None) -> CompletedJob:
        self.queries.append(query)
        self.query_job_configs.append(job_config)
        assert location == "australia-southeast1"
        return CompletedJob()

    def load_table_from_json(
        self, json_rows: list[dict[str, Any]], destination: str, *, job_config: Any
    ) -> CompletedJob:
        self.loaded_rows.extend(json_rows)
        self.destination = destination
        assert job_config == "job-config"
        return CompletedJob()

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.deleted.append(table)


def _archived_response(tmp_path: Path, endpoint: str = "financials") -> dict[str, Any]:
    body = json.dumps(
        {
            "data": [
                {
                    "edinet_code": "E00001",
                    "fiscal_year": 2025,
                    "submit_date": "2026-06-01 09:00",
                    "operating_income": 100,
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    raw = tmp_path / "response.json.gz"
    raw.write_bytes(gzip.compress(body, mtime=0))
    return {
        "run_id": "run-1",
        "source": "edinetdb",
        "endpoint": endpoint,
        "unit_key": "E00001",
        "snapshot_date": "2026-10-12",
        "request_url": "https://example.test/financials",
        "fetched_at": "2026-10-12T00:00:00+00:00",
        "http_status": 200,
        "record_count": 1,
        "min_fiscal_year": 2025,
        "max_fiscal_year": 2025,
        "response_sha256": digest,
        "api_version": "v1",
        "status": "succeeded",
        "error": None,
        "local_raw_path": str(raw),
        "raw_uri": None,
        "manifest_uri": None,
    }


def test_raw_row_preserves_payload_and_lineage(tmp_path: Path) -> None:
    manifest = _archived_response(tmp_path)
    row = subject.row_from_manifest(manifest)

    assert row["raw_object_key"].endswith(manifest["response_sha256"])
    assert row["source"] == "edinetdb"
    assert row["snapshot_date"] == "2026-10-12"
    assert row["payload_json"]["data"][0]["operating_income"] == 100


def test_hash_mismatch_stops_the_load(tmp_path: Path) -> None:
    manifest = _archived_response(tmp_path)
    manifest["response_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="raw hash mismatch"):
        subject.row_from_manifest(manifest)


def test_infrastructure_keeps_raw_and_staging_responsibilities_separate() -> None:
    settings = subject.BigQuerySettings(project="invest-dwh")
    sql = "\n".join(subject.infrastructure_sql(settings))

    assert "invest_raw.source_response" in sql
    assert "payload_json JSON NOT NULL" in sql
    assert "invest_staging.security_master" in sql
    assert "invest_staging.financial_actual" in sql
    assert "invest_staging.jquants_financial_disclosure" in sql
    assert "ingestion_run_id INT64" in sql
    assert "forecast_revenue" in sql
    assert "forecast_operating_income" in sql
    assert "forecast_net_income" in sql
    assert "available_at" in sql
    assert "Asia/Tokyo" in sql
    assert "SAFE.PARSE_DATETIME('%F %H:%M'" in sql
    assert "Trigger" not in sql


def test_merge_is_idempotent_by_raw_object_key() -> None:
    settings = subject.BigQuerySettings(project="invest-dwh")
    sql = subject.merge_sql(settings, "invest-dwh.invest_raw._load_test")

    assert "ON T.raw_object_key = S.raw_object_key" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert "WHEN MATCHED" not in sql


def test_loader_ignores_non_financial_endpoint_and_deletes_temporary_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeBigQueryClient()
    settings = subject.BigQuerySettings(project="invest-dwh")
    loader = subject.BigQueryFinancialHistoryLoader(client, settings, maximum_bytes_billed=10 * 1024**3)
    monkeypatch.setattr(subject, "_load_job_config", lambda: "job-config")

    loaded = loader.load([_archived_response(tmp_path), _archived_response(tmp_path, endpoint="segments")])

    assert loaded == 1
    assert len(client.loaded_rows) == 1
    assert "MERGE" in client.queries[-1]
    assert client.destination is not None
    assert client.deleted == [client.destination]
    assert client.query_job_configs[-1].maximum_bytes_billed == 10 * 1024**3


def test_loader_rejects_missing_query_cost_ceiling() -> None:
    with pytest.raises(ValueError, match="maximum_bytes_billed must be positive"):
        subject.BigQueryFinancialHistoryLoader(
            FakeBigQueryClient(),
            subject.BigQuerySettings(project="invest-dwh"),
            maximum_bytes_billed=0,
        )


def test_latest_jquants_watermark_uses_loaded_ingestion_run() -> None:
    client = FakeBigQueryClient()
    client.query = lambda query, **kwargs: RowJob([{"ingestion_run_id": 42}])  # type: ignore[method-assign]
    loader = subject.BigQueryFinancialHistoryLoader(
        client,
        subject.BigQuerySettings(project="invest-dwh"),
        maximum_bytes_billed=10 * 1024**3,
    )

    assert loader.latest_jquants_ingestion_run_id() == 42


def _jquants_run(raw: Path) -> subject.JQuantsRawRun:
    return subject.JQuantsRawRun(
        ingestion_run_id=42,
        raw_path=str(raw),
        requested_from=date(2026, 5, 12),
        requested_to=date(2026, 5, 12),
        fetched_count=1,
        started_at=datetime(2026, 5, 12, 9, tzinfo=UTC),
        finished_at=datetime(2026, 5, 12, 10, tzinfo=UTC),
        git_commit_sha="abc123",
    )


def test_jquants_adapter_preserves_ingestion_lineage(tmp_path: Path) -> None:
    records = [
        {
            "Code": "72030",
            "DiscNo": "202605120001",
            "DiscDate": "2026-05-12",
            "DiscTime": "15:00:00",
            "DocType": "FYFinancialStatements_Consolidated_JP",
            "CurPerType": "FY",
            "CurFYEn": "2026-03-31",
            "OP": "100",
            "NxFOP": "120",
        }
    ]
    body = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
    raw = tmp_path / "run-42.json.gz"
    raw.write_bytes(gzip.compress(body, mtime=0))

    row = subject.row_from_jquants_run(_jquants_run(raw))

    assert row["source"] == "jquants"
    assert row["endpoint"] == "financial_summary"
    assert row["ingestion_run_id"] == 42
    assert row["git_commit_sha"] == "abc123"
    assert row["record_count"] == 1
    assert row["fetched_at"] == "2026-05-12T10:00:00+00:00"
    assert row["http_status"] is None
    assert row["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert row["payload_json"] == records


def test_jquants_loader_uses_same_idempotent_raw_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "run.json.gz"
    raw.write_bytes(gzip.compress(b"[]", mtime=0))
    client = FakeBigQueryClient()
    loader = subject.BigQueryFinancialHistoryLoader(
        client,
        subject.BigQuerySettings(project="invest-dwh"),
        maximum_bytes_billed=10 * 1024**3,
    )
    monkeypatch.setattr(subject, "_load_job_config", lambda: "job-config")

    loaded = loader.load_jquants([_jquants_run(raw)])

    assert loaded == 1
    assert client.loaded_rows[0]["raw_object_key"].startswith("jquants:financial_summary:42:")
    assert "MERGE" in client.queries[-1]


def test_point_in_time_checks_cover_information_availability() -> None:
    sql = subject.point_in_time_check_sql(subject.BigQuerySettings(project="invest-dwh"))

    assert "missing_disclosure_identity_or_time" in sql
    assert "fetched_before_available" in sql
    assert "duplicate_disclosure_key" in sql
    assert "forecast_without_fiscal_year_end" in sql


def test_local_manifest_discovery_excludes_run_manifests(tmp_path: Path) -> None:
    unit = tmp_path / "snapshot_date=2026-10-12" / "endpoint=financials" / "unit=E1.json"
    run = tmp_path / "snapshot_date=2026-10-12" / "runs" / "run-1.json"
    unit.parent.mkdir(parents=True)
    run.parent.mkdir(parents=True)
    unit.write_text("{}", encoding="utf-8")
    run.write_text("{}", encoding="utf-8")

    assert subject.discover_manifests(str(tmp_path)) == [str(unit)]
