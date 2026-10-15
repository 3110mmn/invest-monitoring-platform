from __future__ import annotations

from research.edinetdb_source_qualification import (
    aggregate_field_coverage,
    build_missing_field_details,
    evaluate_gate,
    extract_rows,
    longest_consecutive_run,
    reconcile_jquants,
    reconciliation_exceptions,
    select_recent_years,
    summarize_company,
    summarize_quality_dimensions,
)


def annual_row(year: int, **overrides):
    row = {
        "fiscal_year": year,
        "revenue": 1000 + year,
        "cost_of_sales": 600,
        "gross_profit": 400,
        "sga": 200,
        "operating_income": 200,
        "inventories": 50,
        "trade_receivables": 80,
        "cf_operating": 150,
        "doc_id": f"S{year}",
        "submit_date_time": f"{year}-06-01 10:00",
        "edinet_filing_url": f"https://example.test/{year}",
        "accounting_standard": "JP-GAAP",
    }
    row.update(overrides)
    return row


def test_extract_rows_accepts_nested_response_envelopes() -> None:
    assert extract_rows({"data": [{"fiscal_year": 2024}]}) == [{"fiscal_year": 2024}]
    assert extract_rows({"data": {"companies": [{"edinet_code": "E00001"}]}}) == [
        {"edinet_code": "E00001"}
    ]


def test_longest_consecutive_run_ignores_duplicates_and_gaps() -> None:
    assert longest_consecutive_run([2020, 2021, 2021, 2023, 2024, 2025]) == 3
    assert longest_consecutive_run([]) == 0


def test_select_recent_years_uses_latest_fiscal_years_per_company() -> None:
    financials = {
        "E00001": [annual_row(2021), annual_row(2024), annual_row(2022), annual_row(2023)]
    }

    selected = select_recent_years(financials, 3)

    assert [row["fiscal_year"] for row in selected["E00001"]] == [2024, 2023, 2022]


def test_quality_diagnostics_keep_basis_traceability_and_missing_reason() -> None:
    row = annual_row(2024, operating_income=None)
    row.update(
        basis="consolidated",
        accounting_standard="JP",
        null_reasons={"operating_income": "not_applicable_industry_practice"},
    )

    dimensions = summarize_quality_dimensions({"E00001": [row]})
    missing = build_missing_field_details({"E00001": [row]})

    assert any(
        item["dimension"] == "basis" and item["value"] == "consolidated"
        for item in dimensions
    )
    assert missing == [
        {
            "edinet_code": "E00001",
            "fiscal_year": 2024,
            "field": "operating_income",
            "basis": "consolidated",
            "accounting_standard": "JP",
            "doc_id": "S2024",
            "submit_date": "2024-06-01 10:00",
            "null_reason": "not_applicable_industry_practice",
        }
    ]


def test_reconciliation_exceptions_adds_relative_difference() -> None:
    rows = [
        {
            "comparison_status": "compared",
            "exact_match": False,
            "difference": -20.0,
            "jquants_value": 1000.0,
        }
    ]

    result = reconciliation_exceptions(rows)

    assert result[0]["difference_pct"] == -2.0


def test_company_summary_measures_continuity_fields_and_traceability() -> None:
    company = {
        "edinet_code": "E00001",
        "securities_code": "1234",
        "name": "Example Retail",
        "industry": "Retail Trade",
    }
    rows = [annual_row(2022), annual_row(2023), annual_row(2024, sga=None)]

    summary = summarize_company(company, rows, [{"segment_name": "Domestic"}])

    assert summary["jpx_code"] == "12340"
    assert summary["has_three_consecutive_years"] is True
    assert summary["sga_coverage_pct"] == 66.67
    assert summary["doc_id_coverage_pct"] == 100.0
    assert summary["has_segments"] is True


def test_gate_requires_continuity_and_required_field_coverage() -> None:
    financials = {
        "E00001": [annual_row(2022), annual_row(2023), annual_row(2024)],
        "E00002": [annual_row(2022), annual_row(2023), annual_row(2024)],
    }
    company_rows = [
        summarize_company(
            {"edinet_code": code, "securities_code": f"123{index}"}, rows, []
        )
        for index, (code, rows) in enumerate(financials.items())
    ]
    coverage = aggregate_field_coverage(financials)

    result = evaluate_gate(company_rows, coverage)

    assert result["continuity_gate_passed"] is True
    assert result["field_gate_passed"] is True
    assert result["adopt_for_backfill"] is True


def test_missing_sga_fails_the_field_gate() -> None:
    rows = [
        annual_row(
            2022,
            sga=None,
            null_reasons={"sga": "not_applicable_accounting_standard"},
        ),
        annual_row(2023, sga=None),
        annual_row(2024),
    ]
    financials = {"E00001": rows}
    company_rows = [
        summarize_company(
            {"edinet_code": "E00001", "securities_code": "1234"}, rows, []
        )
    ]

    coverage = aggregate_field_coverage(financials)
    result = evaluate_gate(company_rows, coverage)

    assert result["field_gate_passed"] is False
    assert "sga" in result["failed_required_fields"]
    assert result["adopt_for_backfill"] is False
    sga = next(row for row in coverage if row["field"] == "sga")
    assert sga["known_null_reason_rows"] == 1
    assert sga["unknown_null_rows"] == 1


def test_reconciliation_keeps_unavailable_instead_of_treating_it_as_match() -> None:
    companies = [{"edinet_code": "E00001", "securities_code": "1234"}]
    financials = {"E00001": [annual_row(2024, revenue=1200, operating_income=100)]}
    jquants = [
        {"jpx_code": "12340", "fiscal_year": 2024, "revenue": 1200, "operating_income": None}
    ]

    result = reconcile_jquants(companies, financials, jquants)

    revenue = next(row for row in result if row["field"] == "revenue")
    operating_income = next(row for row in result if row["field"] == "operating_income")
    assert revenue["exact_match"] is True
    assert operating_income["comparison_status"] == "unavailable"
    assert operating_income["exact_match"] is None
