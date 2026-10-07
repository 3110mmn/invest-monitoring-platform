"""PBR Research Pilotの母集団とEvidence SQLを検証する。"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from research.pbr_value_trap import (  # noqa: E402
    ResearchParameters,
    prepare_research_connection,
    quality_summary,
)

from scripts.build_financial_parquet import (  # noqa: E402
    build_schema as financial_schema,
)
from scripts.build_parquet import PRICE_SCHEMA  # noqa: E402
from scripts.build_security_master import SECURITY_MASTER_SCHEMA  # noqa: E402


def _write_table(root, subpath: str, schema: pa.Schema, rows: list[dict]) -> None:
    target = root / subpath
    target.mkdir(parents=True)
    complete = [{name: row.get(name) for name in schema.names} for row in rows]
    pq.write_table(pa.Table.from_pylist(complete, schema=schema), target / "part-0.parquet")


def _price(code: str, day: int, close: float) -> dict:
    return {
        "target_key": f"{code[:4]}.T",
        "jpx_code": code,
        "source_key": "jquants",
        "obs_date": date(2026, 7, day),
        "raw_close_price": close,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 1000.0,
        "price_basis": "adjusted",
        "adjustment_factor": 1.0,
        "ingestion_run_id": 1,
        "built_at": datetime(2026, 7, day, tzinfo=UTC),
    }


def _financial(code: str, number: str, *, equity: int, net_income: int) -> dict:
    return {
        "jpx_code": code,
        "target_key": f"{code[:4]}.T",
        "source_key": "jquants",
        "disclosure_number": number,
        "disclosed_date": date(2026, 7, 2),
        "disclosed_time": "15:00:00",
        "document_type": "FYFinancialStatements_Consolidated_JP",
        "fiscal_period_type": "FY",
        "period_start": date(2025, 7, 1),
        "period_end": date(2026, 7, 1),
        "fiscal_year_start": date(2025, 7, 1),
        "fiscal_year_end": date(2026, 7, 1),
        "accounting_standard": "JP",
        "reporting_scope": "consolidated",
        "revenue": 1000,
        "operating_income": 100,
        "net_income": net_income,
        "total_assets": 2000,
        "equity": equity,
        "operating_cash_flow": 80,
        "investing_cash_flow": -30,
        "bps": 100.0,
        "source_record_hash": number,
        "ingestion_run_id": 1,
        "built_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


def _security(code: str, name: str) -> dict:
    return {
        "security_key": code,
        "jpx_code": code,
        "target_key": f"{code[:4]}.T",
        "company_name": name,
        "market_code": "0111",
        "market_name": "Prime",
        "sector_17_code": "1",
        "sector_17_name": "製造業",
        "sector_33_code": "5",
        "sector_33_name": "機械",
        "source_key": "jquants",
        "source_date": date(2026, 7, 3),
        "ingestion_run_id": 1,
        "built_at": datetime(2026, 7, 3, tzinfo=UTC),
    }


@pytest.fixture
def research_lake(tmp_path):
    low, high = "11110", "22220"
    prices = [
        _price(low, 1, 100.0),
        _price(low, 3, 80.0),
        _price(high, 1, 100.0),
        _price(high, 3, 120.0),
    ]
    _write_table(
        tmp_path,
        "observed/market_price/year=2026",
        PRICE_SCHEMA,
        prices,
    )
    _write_table(
        tmp_path,
        "observed/financial_summary/year=2026",
        financial_schema(),
        [
            _financial(low, "low", equity=500, net_income=50),
            _financial(high, "high", equity=400, net_income=20),
        ],
    )
    _write_table(
        tmp_path,
        "reference/security_master",
        SECURITY_MASTER_SCHEMA,
        [_security(low, "Low PBR"), _security(high, "High PBR")],
    )
    return tmp_path


def test_parameters_reject_invalid_values():
    with pytest.raises(ValueError, match="pbr_threshold"):
        ResearchParameters.parse(as_of_date="2026-07-03", pbr_threshold=0)

    with pytest.raises(ValueError, match="stale_after_days"):
        ResearchParameters.parse(as_of_date="2026-07-03", stale_after_days=0)


def test_research_base_uses_point_in_time_data_and_calculates_proxies(research_lake):
    parameters = ResearchParameters.parse(as_of_date="2026-07-03")
    connection = prepare_research_connection(str(research_lake), parameters)

    row = connection.execute(
        "SELECT company_name, pbr, annualized_roe_proxy, equity_ratio, "
        "operating_margin, "
        "free_cash_flow_proxy FROM pbr_research_base WHERE security_key = '11110'"
    ).fetchone()

    assert row is not None
    assert row[0] == "Low PBR"
    expected_annualized_roe = 0.1 * 365 / 365
    assert row[1:] == pytest.approx((0.8, expected_annualized_roe, 0.25, 0.1, 50))


def test_candidate_view_filters_without_assigning_an_assessment_label(research_lake):
    parameters = ResearchParameters.parse(as_of_date="2026-07-03")
    connection = prepare_research_connection(str(research_lake), parameters)

    rows = connection.execute(
        "SELECT security_key, company_name, missing_evidence "
        "FROM pbr_candidate_evidence"
    ).fetchall()

    assert rows == [("11110", "Low PBR", "")]
    columns = [item[0] for item in connection.description]
    assert "value_candidate" not in columns
    assert "value_trap" not in columns


def test_quality_summary_reports_unique_universe(research_lake):
    parameters = ResearchParameters.parse(as_of_date="2026-07-03")
    connection = prepare_research_connection(str(research_lake), parameters)

    summary = quality_summary(connection)

    assert summary["universe_count"] == 2
    assert summary["calculable_count"] == 2
    assert summary["below_book_count"] == 1
    assert summary["duplicate_security_count"] == 0
