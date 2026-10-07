"""J-Quants上場銘柄rawから分析用の銘柄マスタParquetを組み立てる。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import connect_database
from app.etl.raw_store import find_raw_locations, read_raw

# 出力先はリポジトリルートに固定する。`Path("data/parquet")` のようなカレント
# ディレクトリ相対にすると、`cd backend` してから実行したときに
# `backend/data/parquet/` へ出る。.gitignore の `data/parquet/` は途中にスラッシュが
# あるためルートに固定されたパターンで、そちらには一致せず、63MBの派生物が
# コミット候補に並んだ。明示的に `--out` を渡したときは、打った通りに解釈する。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "data" / "parquet"

JOB_TYPE = "jquants_equities_master_archive_v1"

SECURITY_MASTER_SCHEMA = pa.schema(
    [
        ("security_key", pa.string()),
        ("jpx_code", pa.string()),
        ("target_key", pa.string()),
        ("company_name", pa.string()),
        ("company_name_english", pa.string()),
        ("product_category_code", pa.string()),
        ("market_code", pa.string()),
        ("market_name", pa.string()),
        ("sector_17_code", pa.string()),
        ("sector_17_name", pa.string()),
        ("sector_33_code", pa.string()),
        ("sector_33_name", pa.string()),
        ("scale_category", pa.string()),
        ("source_key", pa.string()),
        ("source_date", pa.date32()),
        ("ingestion_run_id", pa.int64()),
        ("built_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_rows(records: list[dict[str, Any]], run_id: int) -> list[dict[str, Any]]:
    built_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for record in records:
        code = _text(record, "Code")
        name = _text(record, "CoName")
        if code is None or name is None:
            continue
        source_date = _text(record, "Date")
        rows.append(
            {
                "security_key": code,
                "jpx_code": code,
                # 普通株・ETF等の標準5桁Codeだけ`.T`へ写す。優先株等を推測しない。
                "target_key": f"{code[:4]}.T"
                if len(code) == 5 and code.endswith("0")
                else None,
                "company_name": name,
                "company_name_english": _text(record, "CoNameEn"),
                "product_category_code": _text(record, "ProdCat"),
                "market_code": _text(record, "Mkt"),
                "market_name": _text(record, "MktNm"),
                "sector_17_code": _text(record, "S17"),
                "sector_17_name": _text(record, "S17Nm"),
                "sector_33_code": _text(record, "S33"),
                "sector_33_name": _text(record, "S33Nm"),
                "scale_category": _text(record, "ScaleCat"),
                "source_key": "jquants",
                "source_date": datetime.strptime(source_date, "%Y-%m-%d").date()
                if source_date
                else None,
                "ingestion_run_id": run_id,
                "built_at": built_at,
            }
        )
    return rows


def publish(local_dir: Path, destination: str) -> None:
    result = subprocess.run(
        [
            "gcloud",
            "storage",
            "rsync",
            "--recursive",
            "--delete-unmatched-destination-objects",
            str(local_dir),
            destination,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:500])


def main() -> int:
    parser = argparse.ArgumentParser(description="銘柄マスタParquetをrawから構築")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--publish", metavar="GS_URI")
    args = parser.parse_args()

    latest: dict[str, dict[str, Any]] = {}
    project_root = PROJECT_ROOT
    connection = connect_database(read_only=True)
    files = 0
    try:
        # 公開前に失われたrawは除外する。最新の取込が失われていた場合、ここで落ちると
        # 銘柄マスタだけが作れなくなる。1つ前の無事なsnapshotへ退く方がよい。
        locations = find_raw_locations(connection, JOB_TYPE, project_root=project_root)
        if locations:
            # 現在の銘柄集合を表すsnapshotなので、最新の成功取得だけを採用する。
            location = locations[-1]
            records = read_raw(
                location, project_root=project_root, raw_dir=settings.raw_data_dir
            )
            files = 1
            latest = {
                row["security_key"]: row
                for row in build_rows(records, location.ingestion_run_id)
            }
    finally:
        connection.close()

    if not files:
        raise SystemExit(f"rawが見つかりません: {JOB_TYPE}")

    out_dir = args.out / "reference" / "security_master"
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(list(latest.values()), schema=SECURITY_MASTER_SCHEMA),
        out_dir / "part-0.parquet",
        compression="zstd",
    )
    print(f"raw {files}ファイル → {len(latest):,}銘柄")
    print(f"出力先: {out_dir}")
    if args.publish:
        destination = f"{args.publish.rstrip('/')}/reference/security_master"
        publish(out_dir, destination)
        print(f"公開先: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
