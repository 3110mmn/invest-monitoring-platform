"""Generic predicate evaluation; metric formulae belong to analytics SQL/dbt."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from analytics.runner import load_definition
from app.analytics_connection import analytics_query_lock

DEFINITION_VERSION = "financial-metric-comparison-v1"


def read_inputs(
    connection: duckdb.DuckDBPyConnection,
    target_key: str,
    metric_key: str,
    comparison_type: str,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT * FROM ("
        + load_definition("metric_comparison_input")
        + ") inputs WHERE target_key = ? AND metric_key = ? AND comparison_type = ?"
    )
    with analytics_query_lock():
        cursor = connection.execute(sql, [target_key, metric_key, comparison_type])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def evaluate(row: dict[str, Any], operator: str, threshold: Decimal | None, as_of: date) -> dict[str, Any]:
    """Compare a prepared value with a rule. Missing data never means non-fire."""
    reason = str(row["input_status"])
    if row["available_on"] > as_of:
        reason = "not_available_yet"
    observed = Decimal(str(row["observed_value"])) if row["observed_value"] is not None else None
    reference = Decimal(str(row["reference_value"])) if row["reference_value"] is not None else None
    current = Decimal(str(row["current_value"])) if row["current_value"] is not None else None
    fired = False
    if reason == "ok":
        if observed is None or not observed.is_finite():
            reason = "nonfinite_value"
        elif operator == "lte":
            fired = threshold is not None and observed <= threshold
        elif operator == "gte":
            fired = threshold is not None and observed >= threshold
        elif operator == "crosses_below":
            fired = reference is not None and current is not None and reference >= 0 > current
        elif operator == "crosses_above":
            fired = reference is not None and current is not None and reference <= 0 < current
        else:
            reason = "unsupported_operator"
    status = "unavailable" if reason != "ok" else ("fired" if fired else "not_fired")
    evidence = json.loads(json.dumps(row, default=str, ensure_ascii=False))
    evidence.update(
        {
            "operator": operator,
            "threshold_value": str(threshold) if threshold is not None else None,
            "sql_sha256": hashlib.sha256(load_definition("metric_comparison_input").encode()).hexdigest(),
        }
    )
    encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
    event_key = ":".join(
        str(row.get(key))
        for key in (
            "security_key",
            "fiscal_year_end",
            "actual_disclosure_number",
            "reporting_scope",
            "accounting_standard",
            "metric_key",
            "comparison_type",
        )
    )
    return {
        "status": status,
        "reason": reason,
        "observed_value": observed,
        "reference_value": reference,
        "evidence": evidence,
        "evidence_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "event_key": event_key,
        "definition_version": DEFINITION_VERSION,
    }
