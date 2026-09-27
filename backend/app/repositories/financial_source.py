"""財務の読み出し。**経路は1本で、常に分析層（Parquet）を読む。**

価格と同じ方針。PostgreSQLはControl Plane、ParquetはData Plane。
`financial_disclosure` / `financial_summary` を読む経路は置かない。

開示の同一性は `disclosure_number` で表す。PostgreSQLの `disclosure_id` はsurrogate key
なので分析層には書いていない。訂正開示は開示番号が別なので、畳まれずに別レコードとして
残る。どれが最新かは利用側が決める。

開示種別によって値が入る列が入れ替わる規則（FY開示は今期予想を持たず翌期予想を持つ、
配当修正だけの開示は実績を持たない）は、読み出し側の関心事なのでここに置く。
"""

from __future__ import annotations

from typing import Any

import duckdb

from app.analytics_connection import analytics_query_lock

# 各区分が「入っている」と判定するための列。いずれかが非NULLなら、その開示は当該区分を
# 持つ。業績予想と配当予想は会社が別々に開示するため分ける。業績予想を出さず配当予想
# だけ出す企業があり、同じ括りにすると「予想はあるが中身が空」に見えてしまう。
CURRENT_FORECAST_COLUMNS = (
    "forecast_revenue",
    "forecast_operating_income",
    "forecast_ordinary_income",
    "forecast_net_income",
    "forecast_eps",
)
DIVIDEND_FORECAST_COLUMNS = ("forecast_annual_dividend_per_share",)
# 配当修正や業績予想の修正だけの開示はこれらを持たない。最新の開示がそのまま最新の
# 実績とは限らない。
ACTUAL_COLUMNS = (
    "revenue",
    "operating_income",
    "ordinary_income",
    "net_income",
    "eps",
    "total_assets",
    "equity",
    "bps",
)
NEXT_FORECAST_COLUMNS = (
    "next_forecast_revenue",
    "next_forecast_operating_income",
    "next_forecast_ordinary_income",
    "next_forecast_net_income",
    "next_forecast_eps",
)

# 開示の新しい順。同日の複数開示は時刻、それも同じなら開示番号で決める。
_LATEST_FIRST = "ORDER BY disclosed_date DESC, disclosed_time DESC, disclosure_number DESC"

# 分析層にしか無い列。APIの応答には含めない。`built_at` はParquetを組んだ時刻で、
# 開示の事実ではない。`source_record_hash` は取込の冪等性のためのもの。
_INTERNAL_COLUMNS = ("built_at", "source_record_hash", "jpx_code", "target_key")


class ParquetFinancialSource:
    """財務開示をDuckDB経由でParquetから読む。

    接続はアプリのlifespanが持つものを使い回す。価格と同じ接続で、`financial_disclosure`
    viewが張られている。
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.conn = connection

    @property
    def _columns(self) -> list[str]:
        names = [
            description[0]
            for description in self.conn.execute("SELECT * FROM financial_disclosure LIMIT 0").description
        ]
        return [name for name in names if name not in _INTERNAL_COLUMNS]

    def _query(self, target: dict[str, Any], *, where: str = "", limit: int | None = None) -> list[dict[str, Any]]:
        # 列一覧の取得から結果のfetchまでを同じlock内に置く。途中で別リクエストが
        # executeするとdescriptionとresult setの両方が別クエリのものになる。
        with analytics_query_lock():
            columns = self._columns
            sql = f"SELECT {', '.join(columns)} FROM financial_disclosure WHERE target_key = ?"
            params: list[Any] = [target["target_key"]]
            if where:
                sql += f" AND ({where})"
            sql += f" {_LATEST_FIRST}"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.conn.execute(sql, params).fetchall()
        # `target_id` は分析層に無い。APIの応答形を保つためControl Planeの値で埋める。
        return [dict(zip(columns, row, strict=True), target_id=target["target_id"]) for row in rows]

    @staticmethod
    def _has_any(columns: tuple[str, ...]) -> str:
        return " OR ".join(f"{column} IS NOT NULL" for column in columns)

    def find_disclosures(self, target: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
        """銘柄の開示履歴を新しい順に返す。訂正開示も別レコードとして含む。"""
        return self._query(target, limit=limit)

    def find_forecast_history(self, target: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
        """会社予想を含む開示を新しい順に返す。

        予想は決算短信だけでなく業績予想の修正でも更新される。実績を伴わない開示も
        含めるため、実績列ではなく予想列の有無で抽出する。配当予想だけを修正する開示も
        あるため、業績予想と配当予想のどちらかがあれば対象とする。
        """
        columns = (*CURRENT_FORECAST_COLUMNS, *DIVIDEND_FORECAST_COLUMNS)
        return self._query(target, where=self._has_any(columns), limit=limit)

    @staticmethod
    def _first_with_any(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> dict[str, Any] | None:
        """新しい順に並んだ開示から、指定した列のどれかを持つ最初のものを返す。"""
        for row in rows:
            if any(row.get(column) is not None for column in columns):
                return row
        return None

    def find_latest(self, target: dict[str, Any]) -> dict[str, Any] | None:
        """最新の実績・今期業績予想・翌期業績予想・配当予想を組み立てて返す。

        開示種別によって値が入る列が入れ替わるため、どの区分も「その値を持つ最新の開示」
        から取り、出所の開示も併せて返す。実績も例外ではない。配当修正だけの開示が
        最新であっても、`latest_actual` には実績を含む直近の開示が入る。

        `latest_disclosure` は種別を問わない最新の開示で、最終更新日の表示に使う。
        実績値の参照には `latest_actual` を使うこと。

        **1銘柄ぶんを1回で読み、区分の振り分けはPythonで行う。** 区分ごとにクエリを
        投げるとParquetを5回スキャンし、1リクエストが3.1秒かかった（実測）。1銘柄の
        開示は2年で数十件しかないので、全件読んで絞る方が速く、判定条件も読みやすい。
        """
        rows = self._query(target)
        return self._latest_from_rows(target, rows)

    def find_overview(self, target: dict[str, Any], limit: int = 50) -> dict[str, Any]:
        """詳細画面用データをParquet 1走査で組み立てる。

        画面は最新値・開示履歴・予想履歴を同時に必要とする。個別に3回取ると、GCS上の
        Parquetを3回走査するうえ、共有接続の直列化で待ち時間も3倍になる。

        **`limit` は新しい順の開示に掛かる。** 予想履歴と最新値もその範囲から導くため、
        `limit` を超えて古い開示しか予想や実績を持たない銘柄では、それらがNULLになる。
        個別APIの `find_forecast_history` は予想を持つ開示だけをlimit件返すので、そこと
        意味が違う。現在の最大は1銘柄25開示（プランの2年窓の上限）で既定の50に収まる。
        窓を広げるときはここを確認する。
        """
        disclosures = self._query(target, limit=limit)
        forecast_columns = (*CURRENT_FORECAST_COLUMNS, *DIVIDEND_FORECAST_COLUMNS)
        forecasts = [row for row in disclosures if any(row.get(column) is not None for column in forecast_columns)]
        return {
            "latest": self._latest_from_rows(target, disclosures),
            "disclosures": disclosures,
            "forecast_history": forecasts,
        }

    def _latest_from_rows(self, target: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """取得済みの開示から最新値を組み立て、再走査を避ける。"""
        if not rows:
            return None
        return {
            "target_id": target["target_id"],
            "latest_disclosure": rows[0],
            "latest_actual": self._first_with_any(rows, ACTUAL_COLUMNS),
            "current_forecast": self._first_with_any(rows, CURRENT_FORECAST_COLUMNS),
            "next_forecast": self._first_with_any(rows, NEXT_FORECAST_COLUMNS),
            "dividend_forecast": self._first_with_any(rows, DIVIDEND_FORECAST_COLUMNS),
        }
