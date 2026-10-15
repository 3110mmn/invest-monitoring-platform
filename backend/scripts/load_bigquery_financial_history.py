"""GCS上のEDINET DB rawをBigQueryのraw/stagingへロードする。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.bigquery_financial_history import (
    BigQueryFinancialHistoryLoader,
    BigQuerySettings,
    load_from_manifest_location,
)
from app.config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifests",
        default=None,
        help="EDINET DB unit manifestのroot。未指定ならRAW_DATA_URI/edinetdb/manifests",
    )
    parser.add_argument("--project", default=settings.bigquery_project)
    parser.add_argument("--location", default=settings.bigquery_location)
    parser.add_argument("--raw-dataset", default=settings.bigquery_raw_dataset)
    parser.add_argument("--staging-dataset", default=settings.bigquery_staging_dataset)
    parser.add_argument(
        "--maximum-bytes-billed",
        type=int,
        default=settings.bigquery_maximum_bytes_billed,
        help="1 query jobの課金対象bytes上限。超過見込みなら実行前に拒否する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.project:
        print("BIGQUERY_PROJECTまたは--projectを指定してください", file=sys.stderr)
        return 1
    manifest_location = args.manifests
    if manifest_location is None:
        if not settings.raw_data_uri:
            print("RAW_DATA_URIまたは--manifestsを指定してください", file=sys.stderr)
            return 1
        manifest_location = f"{settings.raw_data_uri.rstrip('/')}/edinetdb/manifests"

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
    summary = load_from_manifest_location(loader, manifest_location)
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
