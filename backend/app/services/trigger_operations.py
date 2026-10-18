"""Application service shared by the Trigger API and scheduled workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from app.repositories.trigger_repository import TriggerRepository
from app.services.trigger import ComparisonFactsUnavailable, evaluate_conditions, fetch_comparison_facts


@dataclass(frozen=True)
class EvaluationSummary:
    rule_id: int
    target_count: int
    fetched: int
    evaluated: int
    inserted: int


class RuleNotFoundError(LookupError):
    pass


class RuleInactiveError(ValueError):
    pass


def evaluate_rule(
    repo: TriggerRepository,
    *,
    rule_id: int,
    as_of: date,
    mode: str,
    settings: Any,
    analytics_connection: Any = None,
    orchestration_run_id: str | None = None,
) -> EvaluationSummary:
    """Evaluate one rule and persist immutable results.

    The service owns the operational behavior so HTTP requests and Airflow runs
    cannot drift into two different implementations.
    """
    rule = repo.rule(rule_id)
    if rule is None:
        raise RuleNotFoundError("ルールがありません")
    if not rule["is_active"]:
        raise RuleInactiveError("停止済みのルールです")

    targets = repo.scope_targets(rule, as_of)
    target_by_key = {str(target["target_key"]): target for target in targets}
    target_keys = list(target_by_key)
    if not target_keys:
        return EvaluationSummary(rule_id, 0, 0, 0, 0)

    conditions = rule["conditions"]
    comparisons = [(condition["metric_key"], condition["comparison_type"]) for condition in conditions]
    facts = fetch_comparison_facts(
        target_keys=target_keys,
        comparisons=comparisons,
        as_of=as_of,
        settings=settings,
        connection=analytics_connection,
    )

    start = rule["created_at"].astimezone(ZoneInfo("Asia/Tokyo")).date()
    rows = []
    for original in facts.rows:
        row = dict(original)
        if len(conditions) == 1:
            row.setdefault("metric_key", conditions[0]["metric_key"])
            row.setdefault("comparison_type", conditions[0]["comparison_type"])
        if row["available_on"] <= as_of and (mode == "replay" or row["actual_disclosed_date"] > start):
            rows.append(row)

    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = {}
    for row in rows:
        event_identity = (
            row.get("target_key"),
            row.get("fiscal_year_end"),
            row.get("actual_disclosure_number"),
            row.get("actual_disclosed_date"),
            row.get("reporting_scope"),
            row.get("accounting_standard"),
        )
        grouped.setdefault(event_identity, []).append(row)

    inserted = 0
    for event_rows in grouped.values():
        target = target_by_key.get(str(event_rows[0]["target_key"]))
        if target is None:
            continue
        scoped_rows = [
            {
                **row,
                "trigger_scope_type": rule["scope_type"],
                "trigger_scope_label": rule["scope_label"],
                "evaluated_target_id": target["target_id"],
            }
            for row in event_rows
        ]
        result = evaluate_conditions(scoped_rows, conditions, rule["logic_operator"], as_of)
        inserted += repo.save_evaluation(
            rule["rule_version_id"],
            target["target_id"],
            mode,
            as_of,
            result,
            orchestration_run_id=orchestration_run_id,
        )
    return EvaluationSummary(rule_id, len(target_keys), facts.fetched, len(grouped), inserted)


def evaluate_active_rules(
    repo: TriggerRepository,
    *,
    as_of: date,
    settings: Any,
    analytics_connection: Any = None,
    orchestration_run_id: str | None = None,
) -> list[EvaluationSummary]:
    """Evaluate every active rule as one scheduled operational batch."""
    return [
        evaluate_rule(
            repo,
            rule_id=int(rule["rule_id"]),
            as_of=as_of,
            mode="live",
            settings=settings,
            analytics_connection=analytics_connection,
            orchestration_run_id=orchestration_run_id,
        )
        for rule in repo.rules()
    ]


__all__ = [
    "ComparisonFactsUnavailable",
    "EvaluationSummary",
    "RuleInactiveError",
    "RuleNotFoundError",
    "evaluate_active_rules",
    "evaluate_rule",
]
