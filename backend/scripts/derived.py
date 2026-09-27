"""Derivedの計算定義を実行して確認する。

結果を保存しない。**計算定義が正本で、結果は都度計算する**という方針に従う。
materializeが必要になるのは、高コスト・複数用途で共有・過去に提示した判断のEvidence、
のいずれかが成立したときで、そのときは保存先と入力snapshotを明示して別途実装する。

使い方:
    python backend/scripts/derived.py daily_return --target 7203.T --limit 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.runner import DEFAULT_PARQUET_GLOB, DERIVED_DIR, connect, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Derivedを計算して表示する（保存しない）")
    parser.add_argument(
        "name",
        nargs="?",
        help="計算定義の名前。省略すると一覧を表示する",
    )
    parser.add_argument("--parquet-glob", default=DEFAULT_PARQUET_GLOB)
    parser.add_argument("--target", help="target_key で絞る（例: 7203.T）")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if not args.name:
        for path in sorted(DERIVED_DIR.glob("*.sql")):
            print(f"  {path.stem}")
        return 0

    connection = connect(args.parquet_glob)
    relation = run(args.name, connection=connection)
    if args.target:
        relation = relation.filter(f"target_key = '{args.target}'")
    relation.order("obs_date DESC").limit(args.limit).show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
