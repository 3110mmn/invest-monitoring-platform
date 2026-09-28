"""分析層のParquetをブラウザのUIで見る。

DuckDBに同梱のUI拡張をローカルで起動し、APIが読むのと**同じview**を張った状態で開く。
`preferred_price` と `financial_disclosure` は接続時に作られるので、採用する観測の
選び方まで含めて画面の値と同じものを見られる。

追加のインストールは要らない。DuckDBは `backend/requirements.txt` に入っており、
UIはそこから読み込む拡張である。

所在はローカルパスでもGCSでもよい。GCSを指す場合は先に
`gcloud auth application-default login` を済ませておく。

使い方:
    python backend/scripts/explore.py                       # data/parquet
    python backend/scripts/explore.py --lake data/demo-parquet
    python backend/scripts/explore.py --lake gs://bucket/lake
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.runner import (
    FINANCIAL_DISCLOSURE_VIEW,
    PREFERRED_PRICE_VIEW,
    connect_lake,
)

DEFAULT_LAKE = "data/parquet"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="分析層のParquetをブラウザのUIで見る（読み取りのみ）"
    )
    parser.add_argument(
        "--lake",
        default=DEFAULT_LAKE,
        help=f"分析層のルート。ローカルパスか gs:// （既定: {DEFAULT_LAKE}）",
    )
    args = parser.parse_args()

    connection = connect_lake(args.lake)
    # 生のParquetも見たいことがあるので、viewとは別に素の入口も用意する。
    for name, variable in (
        ("raw_market_price", "parquet_glob"),
        ("raw_financial_summary", "financial_parquet_glob"),
    ):
        connection.execute(
            f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM "
            f"read_parquet(getvariable('{variable}'), hive_partitioning = true)"
        )

    connection.execute("INSTALL ui")
    connection.execute("LOAD ui")
    connection.execute("CALL start_ui()")
    # `get_ui_url` はtable functionなのでFROM句で呼ぶ。
    url = connection.execute("SELECT * FROM get_ui_url()").fetchone()

    print(f"分析層: {args.lake}")
    print(f"UI: {url[0] if url else 'http://localhost:4213'}")
    print()
    print("用意したview:")
    print(f"  {PREFERRED_PRICE_VIEW:22} 1日1銘柄に絞った価格。APIとDerivedが読むのと同じ")
    print(f"  {FINANCIAL_DISCLOSURE_VIEW:22} 開示。訂正も別レコードとして残っている")
    print("  raw_market_price       Parquetの素の中身（重複や未調整も含む）")
    print("  raw_financial_summary  同上")
    print()
    print("Ctrl+C で終了します。")
    try:
        # UIはバックグラウンドのサーバなので、プロセスを生かしておく必要がある。
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        connection.execute("CALL stop_ui_server()")
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
