"""EDINET DBの過去財務responseをGCS rawへbackfillする。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.etl.edinetdb_backfill import EdinetDbClient, RawArchive, run_backfill


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--industry", default="retail")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument(
        "--endpoints",
        nargs="+",
        default=["financials"],
        choices=("financials", "segments", "expenses"),
        help="companiesは常に取得する。既定は年次financialsだけ",
    )
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        default=date.today(),
        help="同じ日付・endpoint・企業の成功manifestがあれば再取得しない",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="今回許可する最大HTTPリクエスト数（retryを含む）",
    )
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=0.25,
        help="API呼出しの最小間隔",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="GCSへ公開せずローカルstageと再開動作だけ検証する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not settings.edinetdb_api_key:
        print("EDINETDB_API_KEYが未設定です", file=sys.stderr)
        return 1
    if args.sample_size < 1:
        print("--sample-sizeは1以上で指定してください", file=sys.stderr)
        return 1
    configured_limit = settings.edinetdb_requests_per_day
    max_requests = args.max_requests or configured_limit
    if max_requests > configured_limit:
        print(
            f"--max-requestsはEDINETDB_REQUESTS_PER_DAY={configured_limit}以下にしてください",
            file=sys.stderr,
        )
        return 1
    destination = None if args.local_only else settings.raw_data_uri
    if not args.local_only and not destination:
        print("RAW_DATA_URIが未設定です。検証だけなら--local-onlyを指定してください", file=sys.stderr)
        return 1

    client = EdinetDbClient(
        settings.edinetdb_api_key,
        base_url=settings.edinetdb_base_url,
        timeout_seconds=settings.edinetdb_timeout_seconds,
        max_requests=max_requests,
        min_interval_seconds=args.request_interval_seconds,
    )
    archive = RawArchive(
        raw_dir=settings.raw_data_dir,
        destination_base=destination,
        snapshot_date=args.snapshot_date,
    )
    summary = run_backfill(
        client=client,
        archive=archive,
        industry=args.industry,
        sample_size=args.sample_size,
        endpoints=args.endpoints,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
    return 2 if summary.failed_units or summary.stopped_by_request_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
