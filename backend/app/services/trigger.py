"""Generic predicate evaluation; metric formulae belong to analytics SQL/dbt."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb
from google.cloud import bigquery

from analytics.runner import load_definition
from app.analytics_connection import analytics_query_lock

DEFINITION_VERSION = "financial-metric-comparison-v1"
DBT_DEFINITION_VERSION = "dbt-fct-metric-comparison-v1"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def read_inputs(
    connection: duckdb.DuckDBPyConnection,
    target_keys: str | list[str],
    metric_key: str | list[str],
    comparison_type: str | list[str],
) -> list[dict[str, Any]]:
    keys = [target_keys] if isinstance(target_keys, str) else target_keys
    metrics = [metric_key] if isinstance(metric_key, str) else list(metric_key)
    comparisons = (
        [comparison_type] if isinstance(comparison_type, str) else list(comparison_type)
    )
    if not keys or not metrics or not comparisons:
        return []
    targets_in = ", ".join("?" for _ in keys)
    metrics_in = ", ".join("?" for _ in metrics)
    comparisons_in = ", ".join("?" for _ in comparisons)
    sql = (
        "SELECT * FROM ("
        + load_definition("metric_comparison_input")
        + f") inputs WHERE target_key IN ({targets_in})"
        + f" AND metric_key IN ({metrics_in}) AND comparison_type IN ({comparisons_in})"
    )
    with analytics_query_lock():
        cursor = connection.execute(sql, [*keys, *metrics, *comparisons])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class ComparisonFacts:
    """1回の取得でまとめた比較Fact。`(metric_key, comparison_type)`で引く。

    **取得と判定を分ける。** これまでは評価のたびにRule1本ぶんのクエリを投げていた。
    BigQueryは列指向なので`WHERE`で行を絞ってもスキャン量は変わらず、実測では
    `metric_key`と`comparison_type`で絞っても全件でも同じ14,022,305バイトだった。
    つまりRule本数ぶんクエリを投げるのは、同じデータを読み直しているだけである。

    複数条件Rule（ALL / ANY）は条件ごとに指標や比較軸が違うため、1条件1クエリの
    ままだと条件数ぶん課金が増える。先に取得をまとめておけば、条件が増えても
    クエリは増えない。
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._by_comparison: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            metric, comparison = row.get("metric_key"), row.get("comparison_type")
            if metric is None or comparison is None:
                continue
            self._by_comparison.setdefault((str(metric), str(comparison)), []).append(row)
        self.fetched = len(rows)

    def rows_for(self, metric_key: str, comparison_type: str) -> list[dict[str, Any]]:
        """該当する組の行を返す。

        **組で引けない行は落とさない。** 取得は`metric_key`・`comparison_type`で
        既に絞られている。列が欠けた行（fixture等）を黙って捨てると、取得できて
        いるのに0件になり、原因が分からなくなる。組が1つしか要求されていなければ、
        絞り込みは済んでいるとみなして全件を返す。
        """
        if not self._by_comparison:
            return self._rows
        return self._by_comparison.get((metric_key, comparison_type), [])

    @property
    def rows(self) -> list[dict[str, Any]]:
        """取得済みFact。複数Conditionを同じ開示イベントへ束ねる場合に使う。"""
        return self._rows


def read_bigquery_inputs(
    *,
    project: str,
    dataset: str,
    location: str,
    maximum_bytes_billed: int,
    target_keys: str | list[str],
    metric_key: str | list[str],
    comparison_type: str | list[str],
    as_of: date,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Read the reusable dbt Fact using parameterized filters.

    The returned aliases preserve the evaluator's input contract while the
    lineage fields identify BigQuery/dbt as the calculation source.
    """
    if not _IDENTIFIER.fullmatch(project) or not _IDENTIFIER.fullmatch(dataset):
        raise ValueError("BigQuery project/dataset名が不正です")
    keys = [target_keys] if isinstance(target_keys, str) else target_keys
    metrics = [metric_key] if isinstance(metric_key, str) else list(metric_key)
    comparisons = (
        [comparison_type] if isinstance(comparison_type, str) else list(comparison_type)
    )
    if not keys or not metrics or not comparisons:
        return []
    query = f"""
        SELECT
          comparison_key,
          security_key,
          security_key AS target_key,
          fiscal_year_start,
          fiscal_year_end,
          DATE(current_available_at, 'Asia/Tokyo') AS actual_disclosed_date,
          DATE(current_available_at, 'Asia/Tokyo') AS available_on,
          current_disclosure_number AS actual_disclosure_number,
          ingestion_run_id AS actual_ingestion_run_id,
          raw_uri AS actual_source_key,
          source_record_hash AS actual_source_record_hash,
          reporting_scope,
          accounting_standard,
          DATE(reference_available_at, 'Asia/Tokyo') AS prior_disclosed_date,
          reference_disclosure_number AS prior_disclosure_number,
          metric_key,
          comparison_type,
          reference_value,
          current_value,
          observed_value,
          comparison_status AS input_status,
          current_available_at,
          reference_available_at,
          raw_uri,
          response_sha256,
          source_record_hash,
          '{DBT_DEFINITION_VERSION}' AS calculation_definition_version
        FROM `{project}.{dataset}.fct_metric_comparison`
        WHERE security_key IN UNNEST(@target_keys)
          AND metric_key IN UNNEST(@metric_keys)
          AND comparison_type IN UNNEST(@comparison_types)
          AND DATE(current_available_at, 'Asia/Tokyo') <= @as_of
        ORDER BY current_available_at, current_disclosure_number
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ArrayQueryParameter("target_keys", "STRING", keys),
            bigquery.ArrayQueryParameter("metric_keys", "STRING", metrics),
            bigquery.ArrayQueryParameter("comparison_types", "STRING", comparisons),
            bigquery.ScalarQueryParameter("as_of", "DATE", as_of),
        ],
    )
    query_client = client or bigquery.Client(project=project, location=location)
    return [dict(row.items()) for row in query_client.query(query, job_config=job_config).result()]


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
        elif operator == "lt":
            fired = threshold is not None and observed < threshold
        elif operator == "lte":
            fired = threshold is not None and observed <= threshold
        elif operator == "gt":
            fired = threshold is not None and observed > threshold
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
    definition_version = str(row.get("calculation_definition_version") or DEFINITION_VERSION)
    evidence.update(
        {
            "operator": operator,
            "threshold_value": str(threshold) if threshold is not None else None,
            "calculation_definition_version": definition_version,
        }
    )
    if definition_version == DEFINITION_VERSION:
        evidence["sql_sha256"] = hashlib.sha256(load_definition("metric_comparison_input").encode()).hexdigest()
    encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
    comparison_key = row.get("comparison_key")
    if comparison_key:
        event_key = ":".join(
            str(value)
            for value in (
                comparison_key,
                row.get("actual_disclosure_number"),
                row.get("prior_disclosure_number"),
            )
        )
    else:
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
        "definition_version": definition_version,
    }


def evaluate_conditions(
    rows: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    logic_operator: str,
    as_of: date,
) -> dict[str, Any]:
    """同じ対象・開示イベントのConditionを評価し、三値論理でRule結果へ集約する。"""
    by_key = {(str(row.get("metric_key")), str(row.get("comparison_type"))): row for row in rows}
    member_results: list[dict[str, Any]] = []
    for position, condition in enumerate(conditions, start=1):
        key = (str(condition["metric_key"]), str(condition["comparison_type"]))
        row = by_key.get(key)
        child: dict[str, Any]
        if row is None:
            child = {
                "status": "unavailable",
                "reason": "condition_input_missing",
                "observed_value": None,
                "reference_value": None,
                "event_key": None,
                "definition_version": None,
                "evidence_hash": None,
                "evidence": {},
            }
        else:
            child = evaluate(row, condition["operator"], condition["threshold_value"], as_of)
        member_results.append({
            "position": position,
            **condition,
            "status": child["status"],
            "reason": child["reason"],
            "observed_value": child["observed_value"],
            "reference_value": child["reference_value"],
            "source_event_key": child["event_key"],
            "definition_version": child["definition_version"],
            "evidence_hash": child["evidence_hash"],
            "evidence": child["evidence"],
        })

    statuses = [member["status"] for member in member_results]
    if logic_operator == "all":
        status = "not_fired" if "not_fired" in statuses else (
            "unavailable" if "unavailable" in statuses else "fired"
        )
    else:
        status = "fired" if "fired" in statuses else (
            "unavailable" if "unavailable" in statuses else "not_fired"
        )
    reason = {
        "fired": f"{logic_operator}_conditions_fired",
        "not_fired": "condition_not_fired",
        "unavailable": "condition_unavailable",
    }[status]
    first_row = rows[0] if rows else {}
    # 一条件RuleのEvidence契約は維持し、複数条件時だけ共通情報を上位へ置く。
    # 既存のlineage利用側を壊さず、全条件の判定過程は常にconditionsで辿れる。
    evidence = dict(member_results[0]["evidence"]) if len(member_results) == 1 else {
        "target_key": first_row.get("target_key"),
        "fiscal_year_end": first_row.get("fiscal_year_end"),
        "actual_disclosed_date": first_row.get("actual_disclosed_date"),
        "actual_disclosure_number": first_row.get("actual_disclosure_number"),
        "reporting_scope": first_row.get("reporting_scope"),
        "accounting_standard": first_row.get("accounting_standard"),
    }
    evidence.update({
        "logic_operator": logic_operator,
        "conditions": member_results,
    })
    encoded = json.dumps(evidence, sort_keys=True, default=str, ensure_ascii=False)
    evidence = json.loads(encoded)
    if len(member_results) == 1 and member_results[0]["source_event_key"]:
        event_key = str(member_results[0]["source_event_key"])
    else:
        event_identity = {
            "target_key": first_row.get("target_key"),
            "fiscal_year_end": first_row.get("fiscal_year_end"),
            "actual_disclosure_number": first_row.get("actual_disclosure_number"),
            "actual_disclosed_date": first_row.get("actual_disclosed_date"),
            "reporting_scope": first_row.get("reporting_scope"),
            "accounting_standard": first_row.get("accounting_standard"),
            "members": [
                {
                    "position": member["position"],
                    "metric_key": member["metric_key"],
                    "comparison_type": member["comparison_type"],
                    "source_event_key": member["source_event_key"],
                }
                for member in member_results
            ],
        }
        event_key = "multi:" + hashlib.sha256(
            json.dumps(event_identity, sort_keys=True, default=str).encode()
        ).hexdigest()
    versions = sorted({
        str(member["definition_version"])
        for member in member_results
        if member["definition_version"] is not None
    })
    return {
        "status": status,
        "reason": reason,
        "observed_value": member_results[0]["observed_value"] if len(member_results) == 1 else None,
        "reference_value": member_results[0]["reference_value"] if len(member_results) == 1 else None,
        "evidence": evidence,
        "evidence_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "event_key": event_key,
        "definition_version": "+".join(versions) or DEFINITION_VERSION,
    }


class ComparisonFactsUnavailable(RuntimeError):
    """比較Factを取得できなかった。取得元ごとの理由を添える。"""


def fetch_comparison_facts(
    *,
    target_keys: list[str],
    comparisons: list[tuple[str, str]],
    as_of: date,
    settings: Any,
    connection: duckdb.DuckDBPyConnection | None,
) -> ComparisonFacts:
    """必要な比較Factを1回の取得でまとめる。

    `comparisons` は `(metric_key, comparison_type)` の組。複数Rule・複数Conditionを
    まとめて渡せば、クエリは1回で済む。

    **取得元の選択をここへ閉じる。** Routerが分岐を持つと、Conditionを足したときに
    同じ分岐が増える。運用正本はBigQueryで、`BIGQUERY_PROJECT`が未設定のローカル
    開発・fixtureではDuckDBの照合済み定義を読む。
    """
    if not target_keys or not comparisons:
        return ComparisonFacts([])
    metrics = sorted({metric for metric, _ in comparisons})
    kinds = sorted({comparison for _, comparison in comparisons})
    if settings.bigquery_project:
        try:
            rows = read_bigquery_inputs(
                project=settings.bigquery_project,
                dataset=settings.bigquery_marts_dataset,
                location=settings.bigquery_location,
                maximum_bytes_billed=settings.bigquery_maximum_bytes_billed,
                target_keys=target_keys,
                metric_key=metrics,
                comparison_type=kinds,
                as_of=as_of,
            )
        except Exception as exc:
            raise ComparisonFactsUnavailable("BigQueryのTrigger Factを取得できません") from exc
    else:
        if connection is None:
            raise ComparisonFactsUnavailable("分析データへ接続できません")
        rows = read_inputs(connection, target_keys, metrics, kinds)
    return ComparisonFacts(rows)
