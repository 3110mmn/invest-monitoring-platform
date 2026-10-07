"""Derivedの計算定義を実行して確認する。

結果を保存しない。**計算定義が正本で、結果は都度計算する**という方針に従う。
materializeが必要になるのは、高コスト・複数用途で共有・過去に提示した判断のEvidence、
のいずれかが成立したときで、そのときは保存先と入力snapshotを明示して別途実装する。

使い方:
    python backend/scripts/derived.py daily_return --target 7203.T --limit 10
    python backend/scripts/derived.py point_in_time_pbr --lake data/parquet --latest --below-book
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.runner import DEFAULT_PARQUET_GLOB, DERIVED_DIR, connect, connect_lake, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Derivedを計算して表示する（保存しない）")
    parser.add_argument(
        "name",
        nargs="?",
        help="計算定義の名前。省略すると一覧を表示する",
    )
    parser.add_argument("--parquet-glob", default=DEFAULT_PARQUET_GLOB)
    parser.add_argument(
        "--lake",
        help="価格と財務を含むlakeルート。複数データセットを使うDerivedでは必須",
    )
    parser.add_argument("--target", help="target_key で絞る（例: 7203.T）")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="銘柄ごとの最新行だけを表示する（as_of_dateを持つDerived向け）",
    )
    parser.add_argument(
        "--below-book",
        action="store_true",
        help="PBRが正しく計算でき、1倍未満の行だけを表示する",
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if not args.name:
        for path in sorted(DERIVED_DIR.glob("*.sql")):
            print(f"  {path.stem}")
        return 0

    if args.name == "point_in_time_pbr" and not args.lake:
        parser.error("point_in_time_pbr には --lake が必要です")

    connection = connect_lake(args.lake) if args.lake else connect(args.parquet_glob)
    relation = run(args.name, connection=connection)
    if args.target:
        escaped_target = args.target.replace("'", "''")
        relation = relation.filter(f"target_key = '{escaped_target}'")
    if args.latest:
        if "as_of_date" not in relation.columns:
            parser.error("--latest はas_of_dateを返すDerivedでだけ使えます")
        relation.create_view("_derived_result", replace=True)
        relation = connection.sql(
            "SELECT * FROM _derived_result "
            "QUALIFY ROW_NUMBER() OVER ("
            "PARTITION BY security_key ORDER BY as_of_date DESC"
            ") = 1"
        )
    if args.below_book:
        if "is_below_book_value" not in relation.columns:
            parser.error("--below-book はPBRを返すDerivedでだけ使えます")
        relation = relation.filter("calculation_status = 'ok' AND is_below_book_value")
    order_column = "as_of_date" if "as_of_date" in relation.columns else "obs_date"
    relation.order(f"{order_column} DESC").limit(args.limit).show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
