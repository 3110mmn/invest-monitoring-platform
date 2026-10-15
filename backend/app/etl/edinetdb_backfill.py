"""EDINET DBのresponseを、再開可能な単位でGCS rawへ保存する。

PostgreSQLやParquetへはロードしない。ここで保存するのは外部responseそのものと、
取得単位の完了を示すmanifestだけである。正規化・品質判定は後続層の責務とする。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import certifi

from app.etl.raw_publisher import upload_immutable_object

CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
SUPPORTED_ENDPOINTS = ("financials", "segments", "expenses")


class RequestBudgetExhausted(RuntimeError):
    """明示したAPIリクエスト上限に達した。"""


class EdinetDbFetcher(Protocol):
    request_count: int

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> FetchResult: ...


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    request_url: str
    fetched_at: str
    http_status: int
    api_version: str | None


@dataclass(frozen=True)
class BackfillSummary:
    run_id: str
    requested_units: int
    fetched_units: int
    skipped_units: int
    failed_units: int
    api_requests: int
    stopped_by_request_budget: bool


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    """EDINET DBのresponse envelope差を吸収し、行配列を返す。"""
    value = payload
    if isinstance(value, dict):
        value = value.get("data", value)
    if isinstance(value, dict):
        for key in ("companies", "financials", "segments", "expenses", "items", "results"):
            child = value.get(key)
            if isinstance(child, list):
                value = child
                break
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def company_edinet_code(company: dict[str, Any]) -> str | None:
    for key in ("edinet_code", "edinetCode", "code"):
        value = company.get(key)
        if value is None:
            continue
        code = str(value).strip().upper()
        if code.startswith("E"):
            return code
    return None


def _response_metadata(body: bytes) -> tuple[int, int | None, int | None]:
    """件数と年度範囲を返す。解析不能でもraw保存自体は妨げない。"""
    try:
        rows = extract_rows(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0, None, None
    years: list[int] = []
    for row in rows:
        value = row.get("fiscal_year", row.get("fiscalYear", row.get("year")))
        if value is None:
            continue
        try:
            years.append(int(str(value)[:4]))
        except ValueError:
            continue
    return len(rows), min(years) if years else None, max(years) if years else None


class EdinetDbClient:
    """レート制限・再試行・日次request budgetを持つ最小HTTP client。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        timeout_seconds: float,
        max_requests: int,
        min_interval_seconds: float = 0.0,
        max_retries: int = 3,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_requests = max_requests
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.opener = opener
        self.sleeper = sleeper
        self.request_count = 0
        self._last_request_at: float | None = None
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def _wait_for_interval(self) -> None:
        if self._last_request_at is None or self.min_interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            self.sleeper(self.min_interval_seconds - elapsed)

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> FetchResult:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        for attempt in range(self.max_retries + 1):
            if self.request_count >= self.max_requests:
                raise RequestBudgetExhausted(
                    f"EDINET DB request budget exhausted: {self.max_requests}"
                )
            self._wait_for_interval()
            request = urllib.request.Request(url, headers={"X-API-Key": self.api_key})
            self.request_count += 1
            self._last_request_at = time.monotonic()
            try:
                with self.opener(
                    request,
                    timeout=self.timeout_seconds,
                    context=self.ssl_context,
                ) as response:
                    return FetchResult(
                        body=response.read(),
                        request_url=url,
                        fetched_at=datetime.now(UTC).isoformat(),
                        http_status=int(response.status),
                        api_version=response.headers.get("X-API-Version"),
                    )
            except urllib.error.HTTPError as error:
                body = error.read()
                if error.code == 404 and allow_not_found:
                    return FetchResult(
                        body=body,
                        request_url=url,
                        fetched_at=datetime.now(UTC).isoformat(),
                        http_status=404,
                        api_version=error.headers.get("X-API-Version"),
                    )
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt >= self.max_retries:
                    message = body.decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"EDINET DB HTTP {error.code}: {path}: {message}") from error
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                self.sleeper(delay)
            except urllib.error.URLError:
                if attempt >= self.max_retries:
                    raise
                self.sleeper(float(2**attempt))
        raise AssertionError("unreachable")


class RawArchive:
    """ローカルstageとGCS immutable uploadを一つの規則で扱う。"""

    def __init__(
        self,
        *,
        raw_dir: Path,
        destination_base: str | None,
        snapshot_date: date,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        if destination_base is not None and not destination_base.startswith("gs://"):
            raise ValueError("destination_base must start with gs://")
        self.raw_dir = raw_dir
        self.destination_base = destination_base.rstrip("/") if destination_base else None
        self.snapshot_date = snapshot_date
        self.runner = runner

    def _manifest_relative(self, endpoint: str, unit_key: str) -> Path:
        return Path(
            "edinetdb",
            "manifests",
            f"snapshot_date={self.snapshot_date.isoformat()}",
            f"endpoint={endpoint}",
            f"unit={unit_key}.json",
        )

    def _remote_uri(self, relative: Path) -> str:
        if self.destination_base is None:
            raise RuntimeError("GCS destination is not configured")
        return f"{self.destination_base}/{relative.as_posix()}"

    def completed_manifest(self, endpoint: str, unit_key: str) -> dict[str, Any] | None:
        relative = self._manifest_relative(endpoint, unit_key)
        local = self.raw_dir / relative
        if local.is_file():
            manifest = json.loads(local.read_text(encoding="utf-8"))
            return manifest if manifest.get("status") == "succeeded" else None
        if self.destination_base is None:
            return None
        result = self.runner(
            ["gcloud", "storage", "cat", self._remote_uri(relative)],
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
            missing_markers = ("not found", "404", "matched no objects or files")
            if any(marker in stderr.lower() for marker in missing_markers):
                return None
            raise RuntimeError(
                f"failed to inspect completion manifest: {self._remote_uri(relative)}: "
                f"{stderr.strip()}"
            )
        stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        manifest = json.loads(stdout)
        return manifest if manifest.get("status") == "succeeded" else None

    def read_raw_from_manifest(self, manifest: dict[str, Any]) -> bytes:
        local_path = manifest.get("local_raw_path")
        local = self.raw_dir / str(local_path) if local_path else None
        if local is not None and local.is_file():
            return gzip.decompress(local.read_bytes())
        raw_uri = manifest.get("raw_uri")
        if not raw_uri:
            raise RuntimeError("completed manifest has no readable raw location")
        result = self.runner(["gcloud", "storage", "cat", raw_uri], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"failed to read archived raw: {raw_uri}")
        stdout = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()
        return gzip.decompress(stdout)

    def store_success(
        self,
        *,
        run_id: str,
        source: str,
        endpoint: str,
        unit_key: str,
        result: FetchResult,
    ) -> dict[str, Any]:
        response_hash = hashlib.sha256(result.body).hexdigest()
        relative_raw = Path(
            "edinetdb",
            endpoint,
            f"snapshot_date={self.snapshot_date.isoformat()}",
            f"unit={unit_key}",
            f"response-{response_hash}.json.gz",
        )
        local_raw = self.raw_dir / relative_raw
        local_raw.parent.mkdir(parents=True, exist_ok=True)
        local_raw.write_bytes(gzip.compress(result.body, mtime=0))

        row_count, min_year, max_year = _response_metadata(result.body)
        manifest_relative = self._manifest_relative(endpoint, unit_key)
        local_manifest = self.raw_dir / manifest_relative
        raw_uri = self._remote_uri(relative_raw) if self.destination_base else None
        manifest_uri = self._remote_uri(manifest_relative) if self.destination_base else None
        manifest = {
            "run_id": run_id,
            "source": source,
            "endpoint": endpoint,
            "unit_key": unit_key,
            "snapshot_date": self.snapshot_date.isoformat(),
            "request_url": result.request_url,
            "fetched_at": result.fetched_at,
            "http_status": result.http_status,
            "record_count": row_count,
            "min_fiscal_year": min_year,
            "max_fiscal_year": max_year,
            "response_sha256": response_hash,
            "api_version": result.api_version,
            "status": "succeeded",
            "error": None,
            "local_raw_path": relative_raw.as_posix(),
            "raw_uri": raw_uri,
            "manifest_uri": manifest_uri,
        }
        local_manifest.parent.mkdir(parents=True, exist_ok=True)
        pending_manifest = local_manifest.with_suffix(f".{run_id}.pending")
        pending_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.destination_base:
            assert raw_uri is not None
            assert manifest_uri is not None
            upload_immutable_object(local_raw, raw_uri, runner=self.runner)
            upload_immutable_object(
                pending_manifest, manifest_uri, runner=self.runner
            )
        pending_manifest.replace(local_manifest)
        return manifest

    def store_run_manifest(self, manifest: dict[str, Any]) -> Path:
        run_id = str(manifest["run_id"])
        relative = Path(
            "edinetdb",
            "manifests",
            f"snapshot_date={self.snapshot_date.isoformat()}",
            "runs",
            f"run-{run_id}.json",
        )
        local = self.raw_dir / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.destination_base:
            upload_immutable_object(local, self._remote_uri(relative), runner=self.runner)
        return local


def _company_request(endpoint: str, code: str) -> tuple[str, dict[str, Any] | None]:
    if endpoint == "financials":
        return (
            f"/companies/{code}/financials",
            {"period": "annual", "include_nulls": "true", "include_null_reasons": "true"},
        )
    return f"/companies/{code}/{endpoint}", None


def run_backfill(
    *,
    client: EdinetDbFetcher,
    archive: RawArchive,
    industry: str,
    sample_size: int,
    endpoints: Iterable[str] = ("financials",),
) -> BackfillSummary:
    endpoint_list = tuple(dict.fromkeys(endpoints))
    unknown = set(endpoint_list) - set(SUPPORTED_ENDPOINTS)
    if unknown:
        raise ValueError(f"unsupported endpoints: {sorted(unknown)}")
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    company_unit = f"industry-{industry}-page-1-size-{sample_size}"
    companies_manifest = archive.completed_manifest("companies", company_unit)
    fetched = skipped = failed = 0
    stopped = False

    try:
        if companies_manifest is None:
            companies_result = client.get(
                "/companies",
                {
                    "industry": industry,
                    "sort": "revenue",
                    "order": "desc",
                    "page": 1,
                    "per_page": sample_size,
                },
            )
            companies_manifest = archive.store_success(
                run_id=run_id,
                source="edinetdb",
                endpoint="companies",
                unit_key=company_unit,
                result=companies_result,
            )
            companies_body = companies_result.body
            fetched += 1
        else:
            companies_body = archive.read_raw_from_manifest(companies_manifest)
            skipped += 1
    except RequestBudgetExhausted:
        stopped = True
        companies_body = b'{"data": []}'

    companies = extract_rows(json.loads(companies_body))
    codes = [code for company in companies if (code := company_edinet_code(company))]
    codes = codes[:sample_size]
    if not stopped and len(codes) < sample_size:
        raise RuntimeError(f"companies endpoint returned {len(codes)} usable companies")

    errors: list[dict[str, str]] = []
    for endpoint in endpoint_list:
        for code in codes:
            if archive.completed_manifest(endpoint, code) is not None:
                skipped += 1
                continue
            path, params = _company_request(endpoint, code)
            try:
                result = client.get(path, params, allow_not_found=True)
                archive.store_success(
                    run_id=run_id,
                    source="edinetdb",
                    endpoint=endpoint,
                    unit_key=code,
                    result=result,
                )
                fetched += 1
            except RequestBudgetExhausted:
                stopped = True
                break
            except Exception as error:  # 1社の失敗で成功済みを捨てない
                failed += 1
                errors.append({"endpoint": endpoint, "unit_key": code, "error": str(error)})
        if stopped:
            break

    requested_units = 1 + len(codes) * len(endpoint_list)
    run_manifest = {
        "run_id": run_id,
        "source": "edinetdb",
        "industry": industry,
        "sample_size": sample_size,
        "endpoints": list(endpoint_list),
        "snapshot_date": archive.snapshot_date.isoformat(),
        "requested_units": requested_units,
        "fetched_units": fetched,
        "skipped_units": skipped,
        "failed_units": failed,
        "api_requests": client.request_count,
        "stopped_by_request_budget": stopped,
        "status": "partial" if failed or stopped else "succeeded",
        "errors": errors,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    archive.store_run_manifest(run_manifest)
    return BackfillSummary(
        run_id=run_id,
        requested_units=requested_units,
        fetched_units=fetched,
        skipped_units=skipped,
        failed_units=failed,
        api_requests=client.request_count,
        stopped_by_request_budget=stopped,
    )
