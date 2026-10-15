"""GCS rawをBigQueryの財務履歴層へ冪等にロードする。

GCS rawは再処理原本であり、このモジュールはrawを変更しない。BigQueryにはraw responseの
来歴とpayloadを保存し、staging viewで必要な列だけを型付けする。評価・Trigger判定は行わない。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from app.database import Connection

CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        location: str | None = None,
        job_config: Any = None,
    ) -> Any: ...

    def load_table_from_json(
        self,
        json_rows: Iterable[dict[str, Any]],
        destination: Any,
        *,
        job_config: Any = None,
    ) -> Any: ...

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None: ...


@dataclass(frozen=True)
class BigQuerySettings:
    project: str
    location: str = "australia-southeast1"
    raw_dataset: str = "invest_raw"
    staging_dataset: str = "invest_staging"

    def __post_init__(self) -> None:
        for label, value in (
            ("project", self.project),
            ("raw_dataset", self.raw_dataset),
            ("staging_dataset", self.staging_dataset),
        ):
            if not _IDENTIFIER.fullmatch(value.replace("-", "_")):
                raise ValueError(f"invalid BigQuery {label}: {value}")

    @property
    def raw_table(self) -> str:
        return f"{self.project}.{self.raw_dataset}.source_response"


@dataclass(frozen=True)
class LoadSummary:
    discovered_manifests: int
    loaded_responses: int
    skipped_responses: int


@dataclass(frozen=True)
class JQuantsRawRun:
    ingestion_run_id: int
    raw_path: str
    requested_from: date | None
    requested_to: date | None
    fetched_count: int
    started_at: datetime
    finished_at: datetime | None
    git_commit_sha: str | None


@dataclass(frozen=True)
class PointInTimeCheck:
    check_name: str
    violation_count: int


def _wait(job: Any) -> Any:
    return job.result() if hasattr(job, "result") else job


def _quote(identifier: str) -> str:
    return f"`{identifier}`"


def infrastructure_sql(settings: BigQuerySettings) -> list[str]:
    raw_dataset = _quote(f"{settings.project}.{settings.raw_dataset}")
    staging_dataset = _quote(f"{settings.project}.{settings.staging_dataset}")
    raw_table = _quote(settings.raw_table)
    location = settings.location.replace("'", "")
    return [
        f"CREATE SCHEMA IF NOT EXISTS {raw_dataset} OPTIONS(location='{location}')",
        f"CREATE SCHEMA IF NOT EXISTS {staging_dataset} OPTIONS(location='{location}')",
        f"""
CREATE TABLE IF NOT EXISTS {raw_table} (
  raw_object_key STRING NOT NULL,
  source STRING NOT NULL,
  endpoint STRING NOT NULL,
  unit_key STRING NOT NULL,
  snapshot_date DATE NOT NULL,
  raw_uri STRING,
  manifest_uri STRING,
  response_sha256 STRING NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  loaded_at TIMESTAMP NOT NULL,
  request_url STRING,
  api_version STRING,
  http_status INT64,
  record_count INT64,
  min_fiscal_year INT64,
  max_fiscal_year INT64,
  ingestion_run_id INT64,
  git_commit_sha STRING,
  payload_json JSON NOT NULL
)
PARTITION BY snapshot_date
CLUSTER BY source, endpoint, unit_key
""".strip(),
        f"ALTER TABLE {raw_table} ADD COLUMN IF NOT EXISTS ingestion_run_id INT64",
        f"ALTER TABLE {raw_table} ADD COLUMN IF NOT EXISTS git_commit_sha STRING",
        _security_master_view_sql(settings),
        _financial_actual_view_sql(settings),
        _jquants_financial_disclosure_view_sql(settings),
    ]


def _security_master_view_sql(settings: BigQuerySettings) -> str:
    target = _quote(f"{settings.project}.{settings.staging_dataset}.security_master")
    source = _quote(settings.raw_table)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
  JSON_VALUE(item, '$.edinet_code') AS edinet_code,
  JSON_VALUE(item, '$.sec_code') AS security_code,
  JSON_VALUE(item, '$.name_ja') AS security_name,
  JSON_VALUE(item, '$.name_en') AS security_name_en,
  JSON_VALUE(item, '$.industry') AS industry,
  JSON_VALUE(item, '$.listing_status') AS listing_status,
  SAFE_CAST(JSON_VALUE(item, '$.is_delisted') AS BOOL) AS is_delisted,
  snapshot_date AS valid_from,
  fetched_at,
  source,
  raw_uri,
  response_sha256
FROM {source}, UNNEST(JSON_QUERY_ARRAY(payload_json, '$.data')) AS item
WHERE endpoint = 'companies'
""".strip()


def _financial_actual_view_sql(settings: BigQuerySettings) -> str:
    target = _quote(f"{settings.project}.{settings.staging_dataset}.financial_actual")
    source = _quote(settings.raw_table)
    return f"""
CREATE OR REPLACE VIEW {target} AS
SELECT
  unit_key AS edinet_code,
  JSON_VALUE(item, '$.doc_id') AS document_id,
  SAFE_CAST(JSON_VALUE(item, '$.fiscal_year') AS INT64) AS fiscal_year,
  JSON_VALUE(item, '$.basis') AS reporting_scope,
  JSON_VALUE(item, '$.accounting_standard') AS accounting_standard,
  TIMESTAMP(
    SAFE.PARSE_DATETIME('%F %H:%M', JSON_VALUE(item, '$.submit_date')),
    'Asia/Tokyo'
  ) AS available_at,
  SAFE_CAST(JSON_VALUE(item, '$.revenue') AS NUMERIC) AS revenue,
  SAFE_CAST(JSON_VALUE(item, '$.cost_of_sales') AS NUMERIC) AS cost_of_sales,
  SAFE_CAST(JSON_VALUE(item, '$.gross_profit') AS NUMERIC) AS gross_profit,
  SAFE_CAST(JSON_VALUE(item, '$.sga') AS NUMERIC) AS selling_general_admin_expenses,
  SAFE_CAST(JSON_VALUE(item, '$.operating_income') AS NUMERIC) AS operating_income,
  SAFE_CAST(JSON_VALUE(item, '$.ordinary_income') AS NUMERIC) AS ordinary_income,
  SAFE_CAST(JSON_VALUE(item, '$.net_income') AS NUMERIC) AS net_income,
  SAFE_CAST(JSON_VALUE(item, '$.total_assets') AS NUMERIC) AS total_assets,
  SAFE_CAST(JSON_VALUE(item, '$.net_assets') AS NUMERIC) AS net_assets,
  SAFE_CAST(JSON_VALUE(item, '$.cf_operating') AS NUMERIC) AS operating_cash_flow,
  SAFE_CAST(JSON_VALUE(item, '$.cf_investing') AS NUMERIC) AS investing_cash_flow,
  SAFE_CAST(JSON_VALUE(item, '$.cf_financing') AS NUMERIC) AS financing_cash_flow,
  source,
  fetched_at,
  raw_uri,
  response_sha256
FROM {source}, UNNEST(JSON_QUERY_ARRAY(payload_json, '$.data')) AS item
WHERE endpoint = 'financials'
""".strip()


def _jquants_financial_disclosure_view_sql(settings: BigQuerySettings) -> str:
    """J-Quants決算短信を、予想と実績を同じ開示行のまま展開する。

    比較や評価は行わない。通期決算短信の翌期予想をどの年度へ対応させるかは
    Phase 4のdbtが担う。Phase 3では、開示時点とlineageを失わず型付けする。
    """
    target = _quote(f"{settings.project}.{settings.staging_dataset}.jquants_financial_disclosure")
    source = _quote(settings.raw_table)
    return f"""
CREATE OR REPLACE VIEW {target} AS
WITH expanded AS (
  SELECT
    item,
    ingestion_run_id,
    fetched_at,
    raw_uri,
    response_sha256,
    git_commit_sha
  FROM {source}, UNNEST(JSON_QUERY_ARRAY(payload_json, '$')) AS item
  WHERE source = 'jquants' AND endpoint = 'financial_summary'
)
SELECT
  JSON_VALUE(item, '$.Code') AS jpx_code,
  JSON_VALUE(item, '$.DiscNo') AS disclosure_number,
  JSON_VALUE(item, '$.DocType') AS document_type,
  JSON_VALUE(item, '$.CurPerType') AS fiscal_period_type,
  SAFE_CAST(JSON_VALUE(item, '$.CurPerSt') AS DATE) AS period_start,
  SAFE_CAST(JSON_VALUE(item, '$.CurPerEn') AS DATE) AS period_end,
  SAFE_CAST(JSON_VALUE(item, '$.CurFYSt') AS DATE) AS fiscal_year_start,
  SAFE_CAST(JSON_VALUE(item, '$.CurFYEn') AS DATE) AS fiscal_year_end,
  CASE
    WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision' THEN 'REIT'
    WHEN ARRAY_LENGTH(SPLIT(JSON_VALUE(item, '$.DocType'), '_')) >= 3
      THEN SPLIT(JSON_VALUE(item, '$.DocType'), '_')[SAFE_OFFSET(2)]
    ELSE NULL
  END AS accounting_standard,
  CASE
    WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
      OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
      THEN 'non_consolidated'
    ELSE 'consolidated'
  END AS reporting_scope,
  TIMESTAMP(
    DATETIME(
      SAFE_CAST(JSON_VALUE(item, '$.DiscDate') AS DATE),
      COALESCE(SAFE_CAST(JSON_VALUE(item, '$.DiscTime') AS TIME), TIME '23:59:59')
    ),
    'Asia/Tokyo'
  ) AS available_at,
  SAFE_CAST(JSON_VALUE(item, '$.DiscDate') AS DATE) AS disclosed_date,
  JSON_VALUE(item, '$.DiscTime') AS disclosed_time,
  CASE WHEN CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NCSales') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.Sales') AS NUMERIC) END AS revenue,
  CASE WHEN CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NCOP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.OP') AS NUMERIC) END AS operating_income,
  CASE WHEN CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NCOdP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.OdP') AS NUMERIC) END AS ordinary_income,
  CASE WHEN CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NCNP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.NP') AS NUMERIC) END AS net_income,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.FNCSales') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.FSales') AS NUMERIC) END AS forecast_revenue,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.FNCOP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.FOP') AS NUMERIC) END AS forecast_operating_income,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.FNCNP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.FNP') AS NUMERIC) END AS forecast_net_income,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NxFNCSales') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.NxFSales') AS NUMERIC) END AS next_forecast_revenue,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NxFNCOP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.NxFOP') AS NUMERIC) END AS next_forecast_operating_income,
  CASE WHEN JSON_VALUE(item, '$.DocType') = 'REITEarnForecastRevision'
         OR CONTAINS_SUBSTR(JSON_VALUE(item, '$.DocType'), '_NonConsolidated_')
    THEN SAFE_CAST(JSON_VALUE(item, '$.NxFNCNP') AS NUMERIC)
    ELSE SAFE_CAST(JSON_VALUE(item, '$.NxFNp') AS NUMERIC) END AS next_forecast_net_income,
  ingestion_run_id,
  fetched_at,
  raw_uri,
  response_sha256,
  git_commit_sha,
  TO_HEX(SHA256(TO_JSON_STRING(item))) AS source_record_hash
FROM expanded
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY JSON_VALUE(item, '$.Code'), JSON_VALUE(item, '$.DiscNo')
  ORDER BY ingestion_run_id DESC, fetched_at DESC
) = 1
""".strip()


def point_in_time_check_sql(settings: BigQuerySettings) -> str:
    source = _quote(f"{settings.project}.{settings.staging_dataset}.jquants_financial_disclosure")
    return f"""
WITH checks AS (
  SELECT 'missing_disclosure_identity_or_time' AS check_name, COUNT(*) AS violation_count
  FROM {source}
  WHERE jpx_code IS NULL OR disclosure_number IS NULL
     OR disclosed_date IS NULL OR available_at IS NULL

  UNION ALL

  SELECT 'fetched_before_available' AS check_name, COUNT(*) AS violation_count
  FROM {source}
  WHERE fetched_at < available_at

  UNION ALL

  SELECT 'duplicate_disclosure_key' AS check_name, COUNT(*) AS violation_count
  FROM (
    SELECT jpx_code, disclosure_number
    FROM {source}
    GROUP BY jpx_code, disclosure_number
    HAVING COUNT(*) > 1
  )

  UNION ALL

  SELECT 'forecast_without_fiscal_year_end' AS check_name, COUNT(*) AS violation_count
  FROM {source}
  WHERE (forecast_revenue IS NOT NULL
      OR forecast_operating_income IS NOT NULL
      OR forecast_net_income IS NOT NULL
      OR next_forecast_revenue IS NOT NULL
      OR next_forecast_operating_income IS NOT NULL
      OR next_forecast_net_income IS NOT NULL)
    AND fiscal_year_end IS NULL
)
SELECT check_name, violation_count
FROM checks
ORDER BY check_name
""".strip()


def merge_sql(settings: BigQuerySettings, temporary_table: str) -> str:
    target = _quote(settings.raw_table)
    temporary = _quote(temporary_table)
    columns = (
        "raw_object_key, source, endpoint, unit_key, snapshot_date, raw_uri, manifest_uri, "
        "response_sha256, fetched_at, loaded_at, request_url, api_version, http_status, "
        "record_count, min_fiscal_year, max_fiscal_year, ingestion_run_id, git_commit_sha, "
        "payload_json"
    )
    values = ", ".join(f"S.{name.strip()}" for name in columns.split(","))
    return f"""
MERGE {target} T
USING {temporary} S
ON T.raw_object_key = S.raw_object_key
WHEN NOT MATCHED THEN
  INSERT ({columns}) VALUES ({values})
""".strip()


def _read_command_bytes(runner: CommandRunner, uri: str) -> bytes:
    result = runner(["gcloud", "storage", "cat", uri], capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
        raise RuntimeError(f"failed to read {uri}: {stderr.strip()}")
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


def discover_manifests(location: str, *, runner: CommandRunner = subprocess.run) -> list[str]:
    if location.startswith("gs://"):
        result = runner(["gcloud", "storage", "ls", "--recursive", location], capture_output=True)
        if result.returncode != 0:
            stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
            raise RuntimeError(f"failed to list {location}: {stderr.strip()}")
        output = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        return sorted(
            line.strip() for line in output.splitlines() if "/endpoint=" in line and line.strip().endswith(".json")
        )
    root = Path(location)
    return sorted(str(path) for path in root.rglob("*.json") if "endpoint=" in path.as_posix())


def read_json(location: str, *, runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    data = _read_command_bytes(runner, location) if location.startswith("gs://") else Path(location).read_bytes()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {location}")
    return value


def _raw_bytes(manifest: Mapping[str, Any], *, runner: CommandRunner) -> bytes:
    local = manifest.get("local_raw_path")
    path = Path(str(local)) if local else None
    if path is not None and not path.is_file():
        path = _PROJECT_ROOT / "data/raw" / path
    if path is not None and path.is_file():
        compressed = path.read_bytes()
    elif raw_uri := manifest.get("raw_uri"):
        compressed = _read_command_bytes(runner, str(raw_uri))
    else:
        if path is None:
            raise ValueError("manifest has no raw_uri or local_raw_path")
        raise FileNotFoundError(path)
    return gzip.decompress(compressed)


def row_from_manifest(manifest: Mapping[str, Any], *, runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    body = _raw_bytes(manifest, runner=runner)
    actual_hash = hashlib.sha256(body).hexdigest()
    expected_hash = str(manifest["response_sha256"])
    if actual_hash != expected_hash:
        raise ValueError(f"raw hash mismatch: endpoint={manifest.get('endpoint')} unit={manifest.get('unit_key')}")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("raw response must be a JSON object")
    raw_object_key = ":".join(
        (
            str(manifest["source"]),
            str(manifest["endpoint"]),
            str(manifest["unit_key"]),
            expected_hash,
        )
    )
    return {
        "raw_object_key": raw_object_key,
        "source": manifest["source"],
        "endpoint": manifest["endpoint"],
        "unit_key": manifest["unit_key"],
        "snapshot_date": manifest["snapshot_date"],
        "raw_uri": manifest.get("raw_uri"),
        "manifest_uri": manifest.get("manifest_uri"),
        "response_sha256": expected_hash,
        "fetched_at": manifest["fetched_at"],
        "loaded_at": datetime.now(UTC).isoformat(),
        "request_url": manifest.get("request_url"),
        "api_version": manifest.get("api_version"),
        "http_status": manifest.get("http_status"),
        "record_count": manifest.get("record_count"),
        "min_fiscal_year": manifest.get("min_fiscal_year"),
        "max_fiscal_year": manifest.get("max_fiscal_year"),
        "ingestion_run_id": manifest.get("ingestion_run_id"),
        "git_commit_sha": manifest.get("git_commit_sha"),
        "payload_json": payload,
    }


def find_jquants_financial_runs(
    connection: Connection,
    *,
    after_run_id: int | None = None,
    job_type: str = "jquants_financial_archive_v1",
) -> list[JQuantsRawRun]:
    def as_date(value: Any) -> date | None:
        if value is None or isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    def as_datetime(value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    query = """
        SELECT ingestion_run_id, raw_path, requested_from, requested_to, fetched_count,
               started_at, finished_at, git_commit_sha
        FROM ingestion_run
        WHERE job_type = ? AND status = 'succeeded' AND raw_path IS NOT NULL
    """
    params: tuple[Any, ...] = (job_type,)
    if after_run_id is not None:
        query += " AND ingestion_run_id > ?"
        params += (after_run_id,)
    rows = connection.execute(query + " ORDER BY ingestion_run_id", params).fetchall()
    return [
        JQuantsRawRun(
            ingestion_run_id=int(row["ingestion_run_id"]),
            raw_path=str(row["raw_path"]),
            requested_from=as_date(row["requested_from"]),
            requested_to=as_date(row["requested_to"]),
            fetched_count=int(row["fetched_count"]),
            started_at=as_datetime(row["started_at"]) or datetime.now(UTC),
            finished_at=as_datetime(row["finished_at"]),
            git_commit_sha=row["git_commit_sha"],
        )
        for row in rows
    ]


def _jquants_raw_bytes(
    run: JQuantsRawRun,
    *,
    project_root: Path,
    raw_dir: Path | None,
    runner: CommandRunner,
) -> bytes:
    if run.raw_path.startswith("gs://"):
        cached: Path | None = None
        marker = "/raw/"
        if raw_dir is not None and marker in run.raw_path:
            cached = raw_dir / run.raw_path.split(marker, 1)[1]
        compressed = (
            cached.read_bytes()
            if cached is not None and cached.is_file()
            else _read_command_bytes(runner, run.raw_path)
        )
    else:
        path = Path(run.raw_path)
        if not path.is_absolute():
            path = project_root / path
        compressed = path.read_bytes()
    return gzip.decompress(compressed)


def row_from_jquants_run(
    run: JQuantsRawRun,
    *,
    project_root: Path = _PROJECT_ROOT,
    raw_dir: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    body = _jquants_raw_bytes(run, project_root=project_root, raw_dir=raw_dir, runner=runner)
    payload = json.loads(body)
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"J-Quants financial raw must be a JSON array: run={run.ingestion_run_id}")
    digest = hashlib.sha256(body).hexdigest()
    snapshot = run.requested_to or run.requested_from or run.started_at.date()
    return {
        "raw_object_key": f"jquants:financial_summary:{run.ingestion_run_id}:{digest}",
        "source": "jquants",
        "endpoint": "financial_summary",
        "unit_key": str(run.ingestion_run_id),
        "snapshot_date": snapshot.isoformat(),
        "raw_uri": run.raw_path,
        "manifest_uri": None,
        "response_sha256": digest,
        "fetched_at": (run.finished_at or run.started_at).isoformat(),
        "loaded_at": datetime.now(UTC).isoformat(),
        "request_url": None,
        "api_version": "jquants_financial_archive_v1",
        "http_status": None,
        "record_count": len(payload),
        "min_fiscal_year": None,
        "max_fiscal_year": None,
        "ingestion_run_id": run.ingestion_run_id,
        "git_commit_sha": run.git_commit_sha,
        "payload_json": payload,
    }


class BigQueryFinancialHistoryLoader:
    def __init__(
        self,
        client: BigQueryClient,
        settings: BigQuerySettings,
        *,
        maximum_bytes_billed: int,
    ) -> None:
        if maximum_bytes_billed < 1:
            raise ValueError("maximum_bytes_billed must be positive")
        self.client = client
        self.settings = settings
        self.query_job_config = _query_job_config(maximum_bytes_billed)

    def ensure_infrastructure(self) -> None:
        for statement in infrastructure_sql(self.settings):
            _wait(
                self.client.query(
                    statement,
                    location=self.settings.location,
                    job_config=self.query_job_config,
                )
            )

    def validate_point_in_time(self) -> list[PointInTimeCheck]:
        result = _wait(
            self.client.query(
                point_in_time_check_sql(self.settings),
                location=self.settings.location,
                job_config=self.query_job_config,
            )
        )
        checks = [
            PointInTimeCheck(
                check_name=str(row["check_name"]),
                violation_count=int(row["violation_count"]),
            )
            for row in result
        ]
        violations = [check for check in checks if check.violation_count > 0]
        if violations:
            details = ", ".join(f"{check.check_name}={check.violation_count}" for check in violations)
            raise ValueError(f"point-in-time validation failed: {details}")
        return checks

    def load(self, manifests: Iterable[Mapping[str, Any]], *, runner: CommandRunner = subprocess.run) -> int:
        rows = [
            row_from_manifest(manifest, runner=runner)
            for manifest in manifests
            if manifest.get("status") == "succeeded" and manifest.get("endpoint") in {"companies", "financials"}
        ]
        return self._load_rows(rows)

    def load_jquants(
        self,
        runs: Iterable[JQuantsRawRun],
        *,
        project_root: Path = _PROJECT_ROOT,
        raw_dir: Path | None = None,
        runner: CommandRunner = subprocess.run,
    ) -> int:
        rows = [row_from_jquants_run(run, project_root=project_root, raw_dir=raw_dir, runner=runner) for run in runs]
        return self._load_rows(rows)

    def _load_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        temporary_table = f"{self.settings.project}.{self.settings.raw_dataset}._load_{uuid.uuid4().hex}"
        job_config = _load_job_config()
        try:
            _wait(self.client.load_table_from_json(rows, temporary_table, job_config=job_config))
            _wait(
                self.client.query(
                    merge_sql(self.settings, temporary_table),
                    location=self.settings.location,
                    job_config=self.query_job_config,
                )
            )
        finally:
            self.client.delete_table(temporary_table, not_found_ok=True)
        return len(rows)


def _load_job_config() -> Any:
    from google.cloud import bigquery

    return bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("raw_object_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("endpoint", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("unit_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("snapshot_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("raw_uri", "STRING"),
            bigquery.SchemaField("manifest_uri", "STRING"),
            bigquery.SchemaField("response_sha256", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("fetched_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("loaded_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("request_url", "STRING"),
            bigquery.SchemaField("api_version", "STRING"),
            bigquery.SchemaField("http_status", "INT64"),
            bigquery.SchemaField("record_count", "INT64"),
            bigquery.SchemaField("min_fiscal_year", "INT64"),
            bigquery.SchemaField("max_fiscal_year", "INT64"),
            bigquery.SchemaField("ingestion_run_id", "INT64"),
            bigquery.SchemaField("git_commit_sha", "STRING"),
            bigquery.SchemaField("payload_json", "JSON", mode="REQUIRED"),
        ],
        write_disposition="WRITE_TRUNCATE",
    )


def _query_job_config(maximum_bytes_billed: int) -> Any:
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(maximum_bytes_billed=maximum_bytes_billed)


def load_from_manifest_location(
    loader: BigQueryFinancialHistoryLoader,
    manifest_location: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> LoadSummary:
    locations = discover_manifests(manifest_location, runner=runner)
    manifests = [read_json(location, runner=runner) for location in locations]
    loader.ensure_infrastructure()
    loaded = loader.load(manifests, runner=runner)
    eligible = sum(
        manifest.get("status") == "succeeded" and manifest.get("endpoint") in {"companies", "financials"}
        for manifest in manifests
    )
    return LoadSummary(
        discovered_manifests=len(locations),
        loaded_responses=loaded,
        skipped_responses=len(locations) - eligible,
    )
