"""Compare the dbt Fact with the legacy DuckDB definition on the local overlap."""

from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SQL = ROOT / "backend/analytics/derived/metric_comparison_input.sql"
DEFAULT_GLOB = ROOT / "data/parquet/observed/financial_summary/**/*.parquet"


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["current_disclosure_number"],
        row["metric_key"],
        row["comparison_type"],
    )


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-6)


def _trigger_state(row: dict[str, Any], threshold: float) -> str:
    status = row.get("input_status", row.get("comparison_status"))
    if status != "ok" or row["observed_value"] is None:
        return "unavailable"
    return "fired" if float(row["observed_value"]) <= threshold else "not_fired"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="invest-dwh")
    parser.add_argument("--mart", default="invest_marts.fct_metric_comparison")
    parser.add_argument("--parquet-glob", default=str(DEFAULT_GLOB))
    parser.add_argument("--metric", choices=("revenue", "operating_income", "net_income"))
    parser.add_argument(
        "--comparison",
        choices=("forecast_variance", "yoy", "absolute", "sign_transition"),
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument(
        "--boundary-width",
        type=float,
        default=1.0,
        help="閾値の前後何ポイントを境界サンプルとして表示するか",
    )
    args = parser.parse_args()

    connection = duckdb.connect()
    escaped_glob = args.parquet_glob.replace("'", "''")
    connection.execute(
        "CREATE VIEW financial_disclosure AS "
        f"SELECT * FROM read_parquet('{escaped_glob}', hive_partitioning=true)"
    )
    cursor = connection.execute(LEGACY_SQL.read_text(encoding="utf-8"))
    columns = [column[0] for column in cursor.description]
    legacy = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
    connection.close()

    if args.metric:
        legacy = [row for row in legacy if row["metric_key"] == args.metric]
    if args.comparison:
        legacy = [row for row in legacy if row["comparison_type"] == args.comparison]

    filters = []
    if args.metric:
        filters.append(f"metric_key = '{args.metric}'")
    if args.comparison:
        filters.append(f"comparison_type = '{args.comparison}'")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        SELECT current_disclosure_number, metric_key, comparison_type,
               reporting_scope, accounting_standard, reference_value,
               current_value, observed_value, comparison_status,
               reference_disclosure_number
        FROM `{args.project}.{args.mart}`
        {where_clause}
    """
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=10 * 1024**3)
    warehouse = {
        _key(dict(row.items())): dict(row.items())
        for row in bigquery.Client(project=args.project).query(query, job_config=job_config).result()
    }

    source_drift: list[tuple[Any, ...]] = []
    formula_mismatches: list[tuple[Any, ...]] = []
    trigger_state_mismatches: list[tuple[Any, ...]] = []
    state_counts: Counter[str] = Counter()
    boundary_rows: list[tuple[str, float, str]] = []
    for legacy_row in legacy:
        normalized = {
            **legacy_row,
            "current_disclosure_number": legacy_row["actual_disclosure_number"],
        }
        key = _key(normalized)
        dbt_row = warehouse.get(key)
        if dbt_row is None:
            source_drift.append(key)
            continue
        same_inputs = all(
            (
                _same_number(legacy_row["reference_value"], dbt_row["reference_value"]),
                _same_number(legacy_row["current_value"], dbt_row["current_value"]),
                legacy_row["reporting_scope"] == dbt_row["reporting_scope"],
                legacy_row["accounting_standard"] == dbt_row["accounting_standard"],
                legacy_row["prior_disclosure_number"]
                == dbt_row["reference_disclosure_number"],
            )
        )
        if not same_inputs:
            source_drift.append(key)
            continue
        if not (
            _same_number(legacy_row["observed_value"], dbt_row["observed_value"])
            and legacy_row["input_status"] == dbt_row["comparison_status"]
        ):
            formula_mismatches.append(key)
            continue
        if args.threshold is not None:
            legacy_state = _trigger_state(legacy_row, args.threshold)
            dbt_state = _trigger_state(dbt_row, args.threshold)
            state_counts[dbt_state] += 1
            if legacy_state != dbt_state:
                trigger_state_mismatches.append(key)
            observed_value = dbt_row["observed_value"]
            if (
                dbt_state != "unavailable"
                and abs(float(observed_value) - args.threshold) <= args.boundary_width
            ):
                boundary_rows.append((str(key[0]), float(observed_value), dbt_state))

    print(
        f"legacy_rows={len(legacy)} "
        f"matched={len(legacy) - len(source_drift) - len(formula_mismatches)} "
        f"source_drift={len(source_drift)} formula_mismatched={len(formula_mismatches)}"
    )
    if source_drift:
        print(f"source_drift_sample={source_drift[:5]}")
    if formula_mismatches:
        print(f"formula_mismatch_sample={formula_mismatches[:5]}")
    if args.threshold is not None:
        print(
            f"threshold={args.threshold} trigger_states={dict(state_counts)} "
            f"trigger_state_mismatched={len(trigger_state_mismatches)}"
        )
        print(
            f"boundary_width={args.boundary_width} "
            "boundary_rows="
            f"{sorted(boundary_rows, key=lambda row: abs(row[1] - args.threshold))[:20]}"
        )
    return 1 if formula_mismatches or trigger_state_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
