"""分析層のParquetをブラウザのUIで見る。

**主な用途は、GCS上のlakeが日次で更新されているかの確認である。** そのため既定の
所在は `PARQUET_LAKE`（APIが読むのと同じ設定）で、手元のコピーではない。
`data/parquet` は組み立てたときのまま止まる静的なコピーなので、既定にすると
「更新されていない」と読み違える。実際に一度そうなった。

DuckDBに同梱のUI拡張をローカルで起動し、APIが読むのと**同じview**を張った状態で開く。
`preferred_price` と `financial_disclosure` は接続時に作られるので、採用する観測の
選び方まで含めて画面の値と同じものを見られる。

追加のインストールは要らない。DuckDBは `backend/requirements.txt` に入っており、
UIはそこから読み込む拡張である。

GCSを読むには先に `gcloud auth application-default login` を済ませておく。

使い方:
    python backend/scripts/explore.py                       # PARQUET_LAKE
    python backend/scripts/explore.py --lake ../data/demo-parquet
    python backend/scripts/explore.py --lake ../data/parquet
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.runner import (
    FINANCIAL_DISCLOSURE_VIEW,
    FINANCIAL_SUBPATH,
    PREFERRED_PRICE_VIEW,
    PRICE_SUBPATH,
    connect_lake,
    lake_glob,
    sql_literal,
)
from app.config import settings

# これを超えて古い組み立ては警告する。日次ワークフローは毎日走るが、GitHubの
# スケジューラは数時間ずれることがあり、取得できる新しい値が無い日は作り直しも
# 起きない。1日だと誤報が出るため2日にしている。
STALE_AFTER = timedelta(days=2)


def report_freshness(connection) -> None:
    """いつ組み立てたデータかを出す。

    **手元の `data/parquet` は静的なコピーで、放っておくと古くなる。** 日次ワークフローが
    更新するのはGCS側だけなので、既定のまま開いて「更新されていない」と読み違える。
    開くたびに鮮度を見せて、その取り違えを起こさせない。
    """
    # `preferred_price` は列を絞っているため `built_at` を持たない。素のviewから取る。
    built = connection.execute("SELECT MAX(built_at) FROM raw_market_price").fetchone()
    rows = connection.execute(
        "SELECT source_key, MAX(obs_date) FROM raw_market_price GROUP BY 1 ORDER BY 1"
    ).fetchall()
    disclosed = connection.execute(
        f"SELECT MAX(disclosed_date) FROM {FINANCIAL_DISCLOSURE_VIEW}"
    ).fetchone()

    today = datetime.now(UTC).date()
    print("鮮度:")
    for source_key, latest in rows:
        behind = (today - latest).days if latest else None
        suffix = f"（{behind}日前）" if behind is not None else ""
        print(f"  価格 {source_key:10} 最新 {latest}{suffix}")
    if disclosed and disclosed[0]:
        print(f"  財務            最新開示 {disclosed[0]}（{(today - disclosed[0]).days}日前）")
    if not built or built[0] is None:
        return
    age = datetime.now(UTC) - built[0]
    print(f"  組み立て        {built[0]:%Y-%m-%d %H:%M}（{age.days}日前）")
    if age > STALE_AFTER:
        print()
        print(f"  ※ {age.days}日前の組み立てです。日次ワークフローが更新するのはGCS側")
        print("     だけなので、手元のコピーを見ていないか確認してください。GCSを見て")
        print("     いてこの表示なら、日次ワークフローの失敗を疑ってください。")


def keep_alive() -> None:
    """UIが動いている間プロセスを生かし、若い世代のGCを回し続ける。

    UIはバックグラウンドのサーバなので、mainが抜けると終わってしまう。

    待つついでにGCを回すのは、GCSを読むとfsspecのファイルオブジェクトが循環参照に
    入り、解放が遅れるためである。このスレッドではイベントループが走っていないので
    安全に回収できる。世代0だけなら数ミリ秒で済む。
    """
    while True:
        time.sleep(1)
        gc.collect(0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="分析層のParquetをブラウザのUIで見る（読み取りのみ）"
    )
    parser.add_argument(
        "--lake",
        default=settings.parquet_lake,
        help="分析層のルート。ローカルパスか gs://（既定: PARQUET_LAKE の値）",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="UIのポート。既定は4213。同時に複数開くときに指定する",
    )
    args = parser.parse_args()
    if not args.lake:
        parser.error(
            "分析層の所在が決まりません。backend/.env の PARQUET_LAKE を設定するか、"
            "--lake で指定してください"
        )

    connection = connect_lake(args.lake)
    # 生のParquetも見たいことがあるので、viewとは別に素の入口も用意する。
    # UIは別sessionから接続するため、所在はsession変数ではなくリテラルで埋める。
    for name, subpath in (
        ("raw_market_price", PRICE_SUBPATH),
        ("raw_financial_summary", FINANCIAL_SUBPATH),
    ):
        connection.execute(
            f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet("
            f"{sql_literal(lake_glob(args.lake, subpath))}, hive_partitioning = true)"
        )

    connection.execute("INSTALL ui")
    connection.execute("LOAD ui")
    if args.port is not None:
        connection.execute(f"SET ui_local_port = {int(args.port)}")
    connection.execute("CALL start_ui()")
    # `get_ui_url` はtable functionなのでFROM句で呼ぶ。
    # ポートが塞がっていても `start_ui` は例外を投げず、ここで初めて
    # 「UI server not started」とだけ出る。原因が分かる形にして止める。
    try:
        url = connection.execute("SELECT * FROM get_ui_url()").fetchone()
    except Exception as exc:
        connection.close()
        raise SystemExit(
            f"UIを起動できませんでした: {exc}\n"
            "既に別のexplore.pyが動いていないか確認してください"
            "（lsof -nP -iTCP:4213 -sTCP:LISTEN）。\n"
            "同時に開きたい場合は --port で別のポートを指定します。"
        ) from exc

    print(f"分析層: {args.lake}")
    print(f"UI: {url[0] if url else 'http://localhost:4213'}")
    print()
    report_freshness(connection)
    print("用意したview:")
    print(f"  {PREFERRED_PRICE_VIEW:22} 1日1銘柄に絞った価格。APIとDerivedが読むのと同じ")
    print(f"  {FINANCIAL_DISCLOSURE_VIEW:22} 開示。訂正も別レコードとして残っている")
    print("  raw_market_price       Parquetの素の中身（重複や未調整も含む）")
    print("  raw_financial_summary  同上")
    print()
    print("Ctrl+C で終了します。")
    try:
        keep_alive()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        connection.execute("CALL stop_ui_server()")
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
