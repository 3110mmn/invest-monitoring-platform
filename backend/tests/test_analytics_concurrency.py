"""共有DuckDB接続へ並行アクセスしても結果が混ざらないことを検証する。

DuckDBの1接続は複数スレッドから同時に `execute` すると、result setが別クエリのもので
上書きされる。FastAPIは同期endpointをthread poolで実行するため、銘柄詳細画面が価格と
財務を並行取得するだけでこれが起きた。**画面は500になり、Frontendは「Failed to fetch」と
表示した。** 列数の違う結果を受け取ることで `zip(..., strict=True)` が落ちる経路である。

接続を `cursor()` で分ければ並列に読めるが、入力の所在は `SET VARIABLE` でsessionへ
束縛してあるため引き継がれず、viewの実体が `read_parquet(NULL)` になる
（`app/analytics_connection.py` のdocstring参照）。そのため直列化で解いている。
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics.runner import connect_lake
from app.repositories.financial_source import ParquetFinancialSource
from app.repositories.price_source import ParquetPriceSource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_financial_parquet import (
    build_financial_rows,
    build_schema,
    write_year_partitions,
)
from build_parquet import PRICE_SCHEMA
from build_security_master import SECURITY_MASTER_SCHEMA

TARGET = {"target_id": 1, "target_key": "8697.T", "target_name": "JPX"}

_RAW_DISCLOSURE = {
    "Code": "86970", "DiscNo": "fy", "DiscDate": "2026-04-28", "DiscTime": "15:00:00",
    "DocType": "FYFinancialStatements_Consolidated_IFRS", "CurPerType": "FY",
    "Sales": "198735000000", "EPS": "76.81",
}


def _price_row(days_ago: int) -> dict:
    return {
        "target_key": TARGET["target_key"],
        "jpx_code": "86970",
        "source_key": "jquants",
        "obs_date": date.today() - timedelta(days=days_ago),
        "open_price": 100.0,
        "high_price": 110.0,
        "low_price": 90.0,
        "close_price": 105.0,
        "volume": 1000.0,
        "price_basis": "adjusted",
        "ingestion_run_id": 1,
        "built_at": datetime(2026, 7, 1, tzinfo=UTC),
    }


@pytest.fixture
def lake(tmp_path) -> str:
    schema = build_schema()
    price_partition = tmp_path / "observed" / "market_price" / "year=2026"
    price_partition.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [_price_row(d) for d in range(60)], schema=PRICE_SCHEMA
        ),
        price_partition / "part-0.parquet",
    )
    rows, _, _ = build_financial_rows([_RAW_DISCLOSURE], 1, schema)
    write_year_partitions(rows, tmp_path / "observed" / "financial_summary", schema)
    master = tmp_path / "reference" / "security_master"
    master.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([], schema=SECURITY_MASTER_SCHEMA),
        master / "part-0.parquet",
    )
    return str(tmp_path)


def test_prices_and_financials_can_be_read_at_the_same_time(lake):
    """画面が同時に投げる2種類の読み出しが、互いの結果を壊さないこと。

    直列化が外れると、価格の取り出しが財務の列を受け取って `zip(strict=True)` で
    落ちる。結果の件数と列名の両方を確認する。
    """
    connection = connect_lake(lake)
    prices = ParquetPriceSource(connection)
    financials = ParquetFinancialSource(connection)

    def read_prices(_: int) -> list[dict]:
        return prices.price_history(TARGET, days=90)

    def read_financials(_: int) -> dict:
        return financials.find_overview(TARGET)

    with ThreadPoolExecutor(max_workers=8) as pool:
        price_results = list(pool.map(read_prices, range(12)))
        financial_results = list(pool.map(read_financials, range(12)))
        mixed = [
            pool.submit(read_prices if index % 2 else read_financials, index)
            for index in range(24)
        ]
        outcomes = [future.result() for future in mixed]

    connection.close()

    assert all(len(rows) == 60 for rows in price_results)
    assert all(rows[0]["close_price"] == 105.0 for rows in price_results)
    assert all(len(overview["disclosures"]) == 1 for overview in financial_results)
    assert all(
        overview["latest"]["latest_actual"]["revenue"] == 198735000000
        for overview in financial_results
    )
    # 交互に投げた分も、形が入れ替わっていないこと。
    for outcome in outcomes:
        if isinstance(outcome, list):
            assert {"obs_date", "close_price", "target_id"} <= set(outcome[0])
        else:
            assert {"latest", "disclosures", "forecast_history"} == set(outcome)
