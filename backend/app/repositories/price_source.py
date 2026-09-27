"""価格の読み出し。**経路は1本で、常に分析層（Parquet）を読む。**

PostgreSQLはControl Plane、ParquetはData Plane。「何を監視しているか」はPostgreSQLが
持ち、「その値がいくらだったか」はParquetが持つ。`market_price_observation` を読む経路は
置かない。

公開デモも同じ経路を通す。デモはsyntheticデータなので取得元の利用条件には触れないが、
配備によってPostgreSQLとParquetを選ぶ二本立てにすると、

- 「同じ日に複数の取得元があるときどれを採るか」の答えが2つになる。片方が
  `preferred_price`、もう片方がPostgreSQL側のSQLになり、静かにずれる
- **公開デモが本番で使わない経路を動かすことになる。** 第三者が実際に触るのはデモ側
  なので、逆である

実データとデモの分離は、コードの分岐ではなく**バケットの権限**で担保する。公開APIの
サービスアカウントは実データのバケットへの権限を持たないため、環境変数を取り違えても
実データは読めない。

銘柄の同一性は `security_key`（= `COALESCE(jpx_code, target_key)`）で表す。定義は
`analytics/derived/preferred_price.sql` にある。Control Planeが持つ `target_key` で
引ければ十分なので、`jpx_code` への写像はここでは行わない。以前は文字列操作で
`7203.T` → `72030` を導いていたが、対応は `investment_target_identifier` が持つ情報で
あり、二重管理になっていた。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import duckdb

from app.analytics_connection import analytics_query_lock


class ParquetPriceSource:
    """分析層のParquetをDuckDB経由で読む。

    接続は使い回す。都度張り直すとParquetのフッタを読み直すため、1リクエストあたり
    2.8秒かかる（実測）。使い回せば0.15秒で済む。接続の寿命はアプリのlifespanが持つ。
    """

    # 列名と並びはAPIのレスポンスモデルに合わせる。
    _COLUMNS = (
        "obs_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "price_basis",
        "source_key",
        "ingestion_run_id",
    )

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.conn = connection

    def price_history(self, target: dict[str, Any], *, days: int) -> list[dict[str, Any]]:
        """1銘柄の日次価格を古い順に返す。

        並び順はAPIの契約。画面は先頭を期間の開始日、末尾を最新として扱う。
        """
        cutoff = date.today() - timedelta(days=days)
        with analytics_query_lock():
            rows = self.conn.execute(
                f"SELECT {', '.join(self._COLUMNS)} FROM preferred_price "
                "WHERE target_key = ? AND obs_date >= ? ORDER BY obs_date ASC",
                [target["target_key"], cutoff],
            ).fetchall()
        # `target_id` は分析層に無い。PostgreSQLのsurrogate keyなので書いていない。
        # APIの応答形を保つため、Control Planeから引いた銘柄行の値で埋める。
        return [dict(zip(self._COLUMNS, row, strict=True), target_id=target["target_id"]) for row in rows]

    def latest_prices(self, targets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """渡された銘柄それぞれの最新の観測を返す。

        価格が無い銘柄は行を作らない。欠損をゼロで埋めない方針と揃える。
        """
        by_key = {str(t["target_key"]): t for t in targets}
        if not by_key:
            return []
        placeholders = ", ".join("?" for _ in by_key)
        with analytics_query_lock():
            rows = self.conn.execute(
                f"""
                SELECT target_key, obs_date, close_price, source_key, ingestion_run_id,
                       price_basis
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY security_key ORDER BY obs_date DESC
                    ) AS price_rank
                    FROM preferred_price
                    WHERE target_key IN ({placeholders})
                )
                WHERE price_rank = 1
                """,
                list(by_key),
            ).fetchall()
        latest = [
            {
                "target_id": by_key[target_key]["target_id"],
                "target_key": target_key,
                "target_name": by_key[target_key]["target_name"],
                "target_type": by_key[target_key].get("target_type"),
                "latest_date": obs_date,
                "close_price": close_price,
                "source_key": source_key,
                "ingestion_run_id": run_id,
                "price_basis": basis,
            }
            for target_key, obs_date, close_price, source_key, run_id, basis in rows
        ]
        latest.sort(key=lambda row: str(row["target_name"]))
        return latest
