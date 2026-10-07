"""銘柄マスタとDerivedをDuckDBから読むQuery Service。"""

from __future__ import annotations

from typing import Any

import duckdb

from app.analytics_connection import analytics_query_lock


class ParquetSecuritySource:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.conn = connection

    def find(self, security_key: str) -> dict[str, Any] | None:
        with analytics_query_lock():
            cursor = self.conn.execute(
                """
                SELECT security_key, jpx_code, target_key, company_name,
                       company_name_english, product_category_code,
                       market_code, market_name,
                       sector_17_code, sector_17_name, sector_33_code,
                       sector_33_name, scale_category
                FROM security_master
                WHERE security_key = ?
                LIMIT 1
                """,
                [security_key],
            )
            columns = [item[0] for item in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row, strict=True)) if row else None

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """全銘柄マスタを名称・コードで検索する。投資対象への登録は行わない。"""
        with analytics_query_lock():
            cursor = self.conn.execute(
                """
                SELECT security_key, jpx_code, target_key, company_name,
                       company_name_english, product_category_code,
                       market_code, market_name,
                       sector_17_code, sector_17_name, sector_33_code,
                       sector_33_name, scale_category
                FROM security_master
                WHERE contains(lower(jpx_code), lower(?))
                   OR contains(lower(company_name), lower(?))
                   OR contains(lower(coalesce(company_name_english, '')), lower(?))
                   OR contains(lower(coalesce(target_key, '')), lower(?))
                ORDER BY CASE WHEN jpx_code = ? OR target_key = ? THEN 0 ELSE 1 END,
                         company_name, security_key
                LIMIT ?
                """,
                [query, query, query, query, query, query, limit],
            )
            columns = [item[0] for item in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]
