from __future__ import annotations

import gzip
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from app.etl.edinetdb_backfill import FetchResult, RawArchive, run_backfill


class FakeClient:
    def __init__(self, *, fail_once: set[str] | None = None) -> None:
        self.request_count = 0
        self.fail_once = fail_once or set()

    def get(self, path, params=None, *, allow_not_found=False):
        del params, allow_not_found
        self.request_count += 1
        if path in self.fail_once:
            self.fail_once.remove(path)
            raise RuntimeError("temporary failure")
        if path == "/companies":
            payload = {
                "data": {
                    "companies": [
                        {"edinet_code": "E00001", "name": "One"},
                        {"edinet_code": "E00002", "name": "Two"},
                    ]
                }
            }
        else:
            payload = {
                "data": [
                    {
                        "fiscal_year": 2024,
                        "revenue": 100,
                        "doc_id": f"doc-{path.rsplit('/', 2)[-2]}",
                    }
                ]
            }
        return FetchResult(
            body=json.dumps(payload, separators=(",", ":")).encode(),
            request_url=f"https://example.test{path}",
            fetched_at="2026-10-11T00:00:00+00:00",
            http_status=200,
            api_version="v1",
        )


def archive(tmp_path: Path) -> RawArchive:
    return RawArchive(
        raw_dir=tmp_path / "raw",
        destination_base=None,
        snapshot_date=date(2026, 10, 11),
    )


def test_backfill_stores_unmodified_response_and_required_manifest(tmp_path: Path) -> None:
    client = FakeClient()

    summary = run_backfill(
        client=client,
        archive=archive(tmp_path),
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )

    assert summary.fetched_units == 3
    assert summary.failed_units == 0
    manifests = list((tmp_path / "raw" / "edinetdb" / "manifests").rglob("unit=E00001.json"))
    manifest = json.loads(manifests[0].read_text())
    raw = tmp_path / "raw" / manifest["local_raw_path"]
    assert hashlib_sha256(gzip.decompress(raw.read_bytes())) == manifest["response_sha256"]
    assert manifest["record_count"] == 1
    assert manifest["min_fiscal_year"] == 2024
    assert manifest["max_fiscal_year"] == 2024
    assert manifest["status"] == "succeeded"


def test_same_snapshot_resumes_without_api_requests(tmp_path: Path) -> None:
    first = FakeClient()
    raw_archive = archive(tmp_path)
    run_backfill(
        client=first,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )
    second = FakeClient()

    summary = run_backfill(
        client=second,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )

    assert summary.fetched_units == 0
    assert summary.skipped_units == 3
    assert summary.api_requests == 0


def test_partial_failure_keeps_success_and_next_run_fetches_only_failure(tmp_path: Path) -> None:
    raw_archive = archive(tmp_path)
    first = FakeClient(fail_once={"/companies/E00002/financials"})

    failed = run_backfill(
        client=first,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )

    assert failed.failed_units == 1
    second = FakeClient()
    resumed = run_backfill(
        client=second,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )
    assert resumed.fetched_units == 1
    assert resumed.skipped_units == 2
    assert resumed.failed_units == 0
    assert second.request_count == 1


def test_endpoint_units_are_independently_resumable(tmp_path: Path) -> None:
    raw_archive = archive(tmp_path)
    first = FakeClient()
    run_backfill(
        client=first,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials"],
    )
    second = FakeClient()

    summary = run_backfill(
        client=second,
        archive=raw_archive,
        industry="retail",
        sample_size=2,
        endpoints=["financials", "segments"],
    )

    assert summary.fetched_units == 2
    assert summary.skipped_units == 3
    assert second.request_count == 2


def test_failed_gcs_publish_does_not_create_completed_manifest(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        joined = " ".join(command)
        if command[:3] == ["gcloud", "storage", "cat"]:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not found")
        if command[:3] == ["gcloud", "storage", "cp"] and "manifests" in joined:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="upload failed")
        if command[:4] == ["gcloud", "storage", "objects", "describe"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    raw_archive = RawArchive(
        raw_dir=tmp_path / "raw",
        destination_base="gs://bucket/raw",
        snapshot_date=date(2026, 10, 11),
        runner=runner,
    )

    with pytest.raises(RuntimeError, match="Immutable raw upload failed"):
        run_backfill(
            client=FakeClient(),
            archive=raw_archive,
            industry="retail",
            sample_size=2,
            endpoints=["financials"],
        )

    completed = list((tmp_path / "raw").rglob("unit=industry-retail-page-1-size-2.json"))
    assert completed == []
    assert any("response-" in " ".join(command) for command in commands)


def test_gcloud_empty_prefix_is_treated_as_not_completed(tmp_path: Path) -> None:
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=b"",
            stderr=b"The following URLs matched no objects or files",
        )

    raw_archive = RawArchive(
        raw_dir=tmp_path / "raw",
        destination_base="gs://bucket/raw",
        snapshot_date=date(2026, 10, 11),
        runner=runner,
    )

    assert raw_archive.completed_manifest("financials", "E00001") is None


def hashlib_sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
