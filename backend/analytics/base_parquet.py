"""公開済みParquetを差分更新の土台として読む。

**全再構築はCIでは使えない。** 2年分のrawは価格490 / 財務487ファイルあり、使い捨ての
ランナーにはローカルキャッシュが無いため、GCSから1つずつ読むと30分を超える。既に
Parquetにある分は読み直さず、`ingestion_run_id` の最大値より後のrawだけを足す。

基準線に `ingestion_run_id` を使えるのは、単調増加で、rawとParquetの両方に入っている
ためである。日付を基準にすると、遡って取り込んだ過去分を取りこぼす。

価格と財務で同じ手順なので、ここに1つだけ置く。片方にしか直しが入らない状態を作らない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def load_base_rows(
    location: str, column_names: Sequence[str]
) -> tuple[list[dict[str, Any]], int | None]:
    """公開済みParquetを読み、行と取込実行の上限を返す。

    公開先がまだ空、または読めない場合は (空リスト, None) を返す。呼び出し側は全件構築へ
    まわる。初回の公開や、パスを間違えたときにここで止めない。

    既存の全行をメモリへ載せる。価格2年分215万行でピーク1.5GB（実測）で、GitHubの
    ランナー16GBに対しては余裕がある。行数に比例して増えるので、5年分を超えるあたりで
    DuckDB側でマージして書き出す形へ移す。
    """
    from analytics.runner import open_connection

    # 価格用のviewは張らない。財務Parquetでは列が揃わず失敗し、毎回全再構築へ
    # 落ちてしまう。
    glob = f"{location.rstrip('/')}/**/*.parquet"
    try:
        connection = open_connection(glob)
    except Exception as exc:
        print(f"  既存Parquetを読めないため全件構築します: {str(exc)[:120]}")
        return [], None

    try:
        rows = connection.execute(
            f"SELECT {', '.join(column_names)} FROM "
            "read_parquet(getvariable('parquet_glob'), hive_partitioning = true)"
        ).fetchall()
    except Exception as exc:
        print(f"  既存Parquetを読めないため全件構築します: {str(exc)[:120]}")
        return [], None
    finally:
        connection.close()

    names = list(column_names)
    base = [dict(zip(names, row, strict=True)) for row in rows]
    watermark = max((r["ingestion_run_id"] or 0) for r in base) if base else None
    return base, watermark
