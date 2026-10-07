"""財務サマリーのrawから、分析用のtyped Parquetを組み立てる。

価格（`build_parquet.py`）と同じ方針。**rawが原本でParquetは派生**であり、正規化は
PostgreSQLへのロードと同じ `normalizers` を使う。ここで別の変換を書くと、同じ「財務」が
PostgreSQLとParquetで食い違う。

Parquetには**business key**（`jpx_code`、`disclosure_number`、`source_key`）を持たせ、
PostgreSQLの `disclosure_id` を書かない。内部IDだけを持つと、分析層がServing DBへ依存する。

価格と違い、財務は**同じ銘柄・同じ期に複数の開示がある**（訂正、予想修正）。開示番号が
別なら別レコードとして残し、どれが最新かは利用側が決める。ここで勝手に畳まない。

使い方:
    python backend/scripts/build_financial_parquet.py --out data/parquet
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.base_parquet import load_base_rows
from app.config import settings
from app.database import connect_database
from app.etl.models import FinancialRecord
from app.etl.normalizers import (
    FINANCIAL_INT_FIELDS,
    FINANCIAL_REAL_FIELDS,
    NON_CONSOLIDATED_INT_FIELDS,
    NON_CONSOLIDATED_REAL_FIELDS,
    SHARED_INT_FIELDS,
    SHARED_REAL_FIELDS,
    normalize_jquants_financial,
)
from app.etl.raw_store import iter_raw

# 出力先はリポジトリルートに固定する。`Path("data/parquet")` のようなカレント
# ディレクトリ相対にすると、`cd backend` してから実行したときに
# `backend/data/parquet/` へ出る。.gitignore の `data/parquet/` は途中にスラッシュが
# あるためルートに固定されたパターンで、そちらには一致せず、63MBの派生物が
# コミット候補に並んだ。明示的に `--out` を渡したときは、打った通りに解釈する。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "data" / "parquet"

JQUANTS_SOURCE_KEY = "jquants"

# 開示そのものを表す列。`values` に入らないメタデータ。
DISCLOSURE_FIELDS: list[tuple[str, pa.DataType]] = [
    ("jpx_code", pa.string()),
    ("target_key", pa.string()),
    ("source_key", pa.string()),
    ("disclosure_number", pa.string()),
    ("disclosed_date", pa.date32()),
    ("disclosed_time", pa.string()),
    ("document_type", pa.string()),
    ("fiscal_period_type", pa.string()),
    ("period_start", pa.date32()),
    ("period_end", pa.date32()),
    ("fiscal_year_start", pa.date32()),
    ("fiscal_year_end", pa.date32()),
    ("accounting_standard", pa.string()),
    ("reporting_scope", pa.string()),
    ("source_record_hash", pa.string()),
    ("ingestion_run_id", pa.int64()),
    ("built_at", pa.timestamp("us", tz="UTC")),
]


def build_schema() -> pa.Schema:
    """財務値の列は normalizers の定義から引く。列を手で並べると二重管理になる。

    **正規化が扱う対応表を1つでも取り落とすと、その列が静かに消える。** 実際に
    `SHARED_*`（キャッシュフロー、配当、株式数の9列）を漏らして、正規化は値を作って
    いるのにParquetには入らない状態を作った。列名は連結・非連結で共通なので、4つの
    対応表のキーを合併した集合が全体になる。
    """
    int_names = {**FINANCIAL_INT_FIELDS, **NON_CONSOLIDATED_INT_FIELDS, **SHARED_INT_FIELDS}
    real_names = {
        **FINANCIAL_REAL_FIELDS,
        **NON_CONSOLIDATED_REAL_FIELDS,
        **SHARED_REAL_FIELDS,
    }
    fields = list(DISCLOSURE_FIELDS)
    fields += [(name, pa.int64()) for name in sorted(int_names)]
    fields += [(name, pa.float64()) for name in sorted(real_names)]
    return pa.schema(fields)


def jpx_code_to_target_key(jpx_code: str) -> str | None:
    """`72030` → `7203.T`。末尾が0でないコードは推測しない（優先株など）。"""
    code = jpx_code.strip()
    if len(code) != 5 or not code.endswith("0"):
        return None
    return f"{code[:4]}.T"


def _as_date(value: str | None):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def build_financial_rows(
    records: list[dict[str, Any]], run_id: int, schema: pa.Schema
) -> tuple[list[dict[str, Any]], int, int]:
    """1ファイルぶんのrawをParquet用の行へ変換する。

    戻り値は (行, 正規化できなかった件数, target_keyを引けなかった件数)。
    """
    built_at = datetime.now(UTC)
    value_columns = [
        name for name in schema.names if name not in {f for f, _ in DISCLOSURE_FIELDS}
    ]
    rows: list[dict[str, Any]] = []
    skipped = 0
    unmapped = 0
    for record in records:
        try:
            financial: FinancialRecord = normalize_jquants_financial(record)
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        target_key = jpx_code_to_target_key(financial.jpx_code)
        if target_key is None:
            unmapped += 1
        row: dict[str, Any] = {
            "jpx_code": financial.jpx_code,
            "target_key": target_key,
            "source_key": JQUANTS_SOURCE_KEY,
            "disclosure_number": financial.disclosure_number,
            "disclosed_date": _as_date(financial.disclosed_date),
            "disclosed_time": financial.disclosed_time,
            "document_type": financial.document_type,
            "fiscal_period_type": financial.fiscal_period_type,
            "period_start": _as_date(financial.period_start),
            "period_end": _as_date(financial.period_end),
            "fiscal_year_start": _as_date(financial.fiscal_year_start),
            "fiscal_year_end": _as_date(financial.fiscal_year_end),
            "accounting_standard": financial.accounting_standard,
            "reporting_scope": financial.reporting_scope,
            "source_record_hash": financial.source_record_hash,
            "ingestion_run_id": run_id,
            "built_at": built_at,
        }
        # 未提供項目はNULLのままにする。ゼロで埋めない。
        for column in value_columns:
            row[column] = financial.values.get(column)
        rows.append(row)
    return rows, skipped, unmapped


def deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """同じ開示番号を1行にする。最後の取込を残す。

    同じ開示日を複数回アーカイブすると重複する。訂正開示は**開示番号が別**なので、
    ここで畳まれることはない。
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["source_key"], row["disclosure_number"])
        current = latest.get(key)
        if current is None or (row["ingestion_run_id"] or 0) >= (
            current["ingestion_run_id"] or 0
        ):
            latest[key] = row
    return list(latest.values()), len(rows) - len(latest)


def write_year_partitions(
    rows: list[dict[str, Any]], out_dir: Path, schema: pa.Schema
) -> dict[int, int]:
    """開示日の年で分ける。価格と同じ粒度に揃える。"""
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[row["disclosed_date"].year].append(row)

    written: dict[int, int] = {}
    for year, year_rows in sorted(by_year.items()):
        target = out_dir / f"year={year}"
        target.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(year_rows, schema=schema)
        pq.write_table(table, target / "part-0.parquet", compression="zstd")
        written[year] = len(year_rows)
    return written


def publish_parquet(local_dir: Path, destination: str) -> None:
    """Parquetは派生物なので上書きを許す。rawと違い作り直す前提。"""
    result = subprocess.run(
        [
            "gcloud", "storage", "rsync", "--recursive",
            "--delete-unmatched-destination-objects",
            str(local_dir), destination,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Parquetの公開に失敗しました: {result.stderr.strip()[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="財務サマリーのrawから分析用Parquetを組み立てる"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--job-type", default="jquants_financial_archive_v1")
    parser.add_argument("--publish", metavar="GS_URI", help="出力後にGCSへ同期する")
    parser.add_argument(
        "--base",
        metavar="LOCATION",
        help="土台にする既存Parquetの所在（gs://bucket/lake か ローカルパス）。"
             "指定するとそれより新しいrawだけを読む。日次はこれを使う",
    )
    args = parser.parse_args()
    if args.publish and not args.publish.startswith("gs://"):
        parser.error("--publish は gs:// で始まるURIを指定してください")

    schema = build_schema()
    project_root = PROJECT_ROOT
    all_rows: list[dict[str, Any]] = []
    skipped_total = unmapped_total = files = remote = 0

    watermark: int | None = None
    if args.base:
        all_rows, watermark = load_base_rows(
            f"{str(args.base).rstrip('/')}/observed/financial_summary", schema.names
        )
        print(f"既存Parquet: {len(all_rows):,}行（取込実行 {watermark} まで）")

    connection = connect_database(read_only=True)
    try:
        for location, records in iter_raw(
            connection, args.job_type,
            project_root=project_root, raw_dir=settings.raw_data_dir,
            after_run_id=watermark,
        ):
            rows, skipped, unmapped = build_financial_rows(
                records, location.ingestion_run_id, schema
            )
            all_rows.extend(rows)
            skipped_total += skipped
            unmapped_total += unmapped
            files += 1
            remote += 1 if location.is_remote else 0
    finally:
        connection.close()

    if not files and not all_rows:
        raise SystemExit(f"rawが見つかりません: job_type={args.job_type}")
    if not files:
        # 差分更新で新しいrawが無いのは、開示が無い日などの正常系。作り直さず終える。
        print("新しいrawはありません。Parquetは現状のままです")
        return 0

    all_rows, duplicates = deduplicate(all_rows)
    out_dir = args.out / "observed" / "financial_summary"
    written = write_year_partitions(all_rows, out_dir, schema)

    print(f"raw {files}ファイル（GCS {remote} / ローカル {files - remote}） → {len(all_rows):,}行")
    for year, count in written.items():
        print(f"  year={year}: {count:,}行")
    if duplicates:
        print(f"  重複を除外（同じ開示日を複数回アーカイブ）: {duplicates:,}行")
    if unmapped_total:
        print(f"  target_key未解決（行は保持）: {unmapped_total:,}行")
    if skipped_total:
        print(f"  正規化できず除外: {skipped_total:,}行")
    print(f"出力先: {out_dir}")

    if args.publish:
        destination = f"{args.publish.rstrip('/')}/observed/financial_summary"
        publish_parquet(out_dir, destination)
        print(f"公開先: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
