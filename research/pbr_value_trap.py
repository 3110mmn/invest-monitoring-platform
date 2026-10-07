"""PBR 1倍割れResearch Pilotの実行基盤。

SQLの組み立てとDuckDB viewの登録だけを担う。評価ラベルや売買判断は生成しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
from backend.analytics.runner import connect_lake, run

RESEARCH_DIR = Path(__file__).resolve().parent
SQL_DIR = RESEARCH_DIR / "sql"


@dataclass(frozen=True)
class ResearchParameters:
    """再実行時に固定すべき分析パラメータ。"""

    as_of_date: date
    pbr_threshold: float = 1.0
    stale_after_days: int = 550

    @classmethod
    def parse(
        cls,
        *,
        as_of_date: str,
        pbr_threshold: float = 1.0,
        stale_after_days: int = 550,
    ) -> ResearchParameters:
        parsed_date = date.fromisoformat(as_of_date)
        if pbr_threshold <= 0:
            raise ValueError("pbr_thresholdは0より大きい必要があります")
        if stale_after_days < 1:
            raise ValueError("stale_after_daysは1以上である必要があります")
        return cls(parsed_date, float(pbr_threshold), stale_after_days)


def load_sql(name: str) -> str:
    """Research PilotのSQLを読む。"""
    path = SQL_DIR / f"{name}.sql"
    if not path.is_file():
        raise FileNotFoundError(f"Research SQLがありません: {name}")
    return path.read_text(encoding="utf-8")


def render_sql(definition: str, parameters: ResearchParameters) -> str:
    """検証済みの型だけをSQLリテラルへ変換する。"""
    return (
        definition.replace("$as_of_date", parameters.as_of_date.isoformat())
        .replace("$pbr_threshold", repr(parameters.pbr_threshold))
        .replace("$stale_after_days", str(parameters.stale_after_days))
    )


def prepare_research_connection(
    lake: str,
    parameters: ResearchParameters,
) -> duckdb.DuckDBPyConnection:
    """lakeへ接続し、分析用viewを登録する。

    `point_in_time_pbr`の定義を再利用し、結果自体は永続化しない。
    """
    connection = connect_lake(lake)
    pbr = run("point_in_time_pbr", connection=connection)
    pbr.create_view("point_in_time_pbr_research", replace=True)

    base_sql = render_sql(load_sql("pbr_research_base"), parameters)
    connection.execute(f"CREATE OR REPLACE TEMP VIEW pbr_research_base AS {base_sql}")
    candidate_sql = render_sql(load_sql("pbr_candidate_evidence"), parameters)
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW pbr_candidate_evidence AS {candidate_sql}"
    )
    return connection


def quality_summary(connection: duckdb.DuckDBPyConnection) -> dict[str, int | float]:
    """Notebook冒頭で表示・検証する最小の品質指標を返す。"""
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS universe_count,
            COUNT(*) FILTER (WHERE calculation_status = 'ok') AS calculable_count,
            COUNT(*) FILTER (WHERE is_below_book_value) AS below_book_count,
            COUNT(*) FILTER (WHERE company_name IS NULL) AS missing_security_master,
            COUNT(*) FILTER (WHERE net_income IS NULL) AS missing_net_income,
            COUNT(*) FILTER (WHERE equity IS NULL) AS missing_equity,
            COUNT(*) FILTER (WHERE operating_cash_flow IS NULL) AS missing_operating_cf,
            COUNT(*) - COUNT(DISTINCT security_key) AS duplicate_security_count
        FROM pbr_research_base
        """
    ).fetchone()
    assert row is not None
    columns = [item[0] for item in connection.description]
    return dict(zip(columns, row, strict=True))
