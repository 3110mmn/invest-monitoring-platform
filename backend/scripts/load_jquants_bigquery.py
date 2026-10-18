"""J-Quantsの会社予想・決算短信rawをBigQueryへ冪等ロードして時点整合性を検査する。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.bigquery_financial_history import (
    BigQueryFinancialHistoryLoader,
    BigQuerySettings,
    find_jquants_financial_runs,
)
from app.config import settings
from app.database import connect_database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=settings.bigquery_project)
    parser.add_argument("--location", default=settings.bigquery_location)
    parser.add_argument("--raw-dataset", default=settings.bigquery_raw_dataset)
    parser.add_argument("--staging-dataset", default=settings.bigquery_staging_dataset)
    parser.add_argument("--after-run-id", type=int)
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="BigQueryにロード済みの最大ingestion_run_idより後だけを処理する",
    )
    parser.add_argument(
        "--skip-exit-code",
        type=int,
        default=0,
        help="--only-newで新規rawが無い場合の終了コード（Airflowでは99）",
    )
    parser.add_argument(
        "--maximum-bytes-billed",
        type=int,
        default=settings.bigquery_maximum_bytes_billed,
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw",
        help="GCS rawと同じ内容が手元にある場合の読み取りキャッシュ",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.project:
        print("BIGQUERY_PROJECTまたは--projectを指定してください", file=sys.stderr)
        return 1

    from google.cloud import bigquery

    warehouse_settings = BigQuerySettings(
        project=args.project,
        location=args.location,
        raw_dataset=args.raw_dataset,
        staging_dataset=args.staging_dataset,
    )
    client = bigquery.Client(project=args.project, location=args.location)
    loader = BigQueryFinancialHistoryLoader(
        client,
        warehouse_settings,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    loader.ensure_infrastructure()
    after_run_id = args.after_run_id
    if args.only_new:
        if after_run_id is not None:
            print("--only-newと--after-run-idは同時に指定できません", file=sys.stderr)
            return 2
        after_run_id = loader.latest_jquants_ingestion_run_id()

    connection = connect_database(read_only=True)
    try:
        runs = find_jquants_financial_runs(connection, after_run_id=after_run_id)
    finally:
        connection.close()

    if args.only_new and not runs:
        print(json.dumps({"discovered_runs": 0, "loaded_responses": 0, "skipped": "no_new_raw"}))
        return args.skip_exit_code

    loaded = loader.load_jquants(
        runs,
        project_root=PROJECT_ROOT,
        raw_dir=args.raw_dir,
    )
    checks = loader.validate_point_in_time()
    print(
        json.dumps(
            {
                "discovered_runs": len(runs),
                "loaded_responses": loaded,
                "point_in_time_checks": [check.__dict__ for check in checks],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
