import inspect
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa

from app.routers.domain import triggers as routes
from app.services import trigger as trigger_service
from app.services.trigger import evaluate, read_bigquery_inputs, read_inputs
from scripts.build_financial_parquet import build_schema


class QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def result(self):
        return self.rows


class BigQueryStub:
    def __init__(self, rows):
        self.rows = rows
        self.query_text = ""
        self.job_config = None

    def query(self, query, *, job_config):
        self.query_text = query
        self.job_config = job_config
        return QueryResult(self.rows)


def disclosure(**changes):
    end = changes.get("fiscal_year_end", date(2024, 3, 31))
    return {
        "jpx_code": "99990",
        "target_key": "9999.T",
        "fiscal_period_type": "FY",
        "fiscal_year_start": date(end.year - 1, 4, 1),
        "fiscal_year_end": end,
        "disclosed_date": date(2024, 5, 10),
        "disclosure_number": "forecast",
        "reporting_scope": "consolidated",
        "accounting_standard": "JGAAP",
        "revenue": 1000,
        "operating_income": 80,
        "net_income": 50,
        "next_forecast_revenue": 1200,
        "next_forecast_operating_income": 100,
        "next_forecast_net_income": 70,
        "ingestion_run_id": 1,
        **changes,
    }


def lake(*extra):
    table = pa.Table.from_pylist(
        [
            disclosure(),
            disclosure(
                fiscal_year_end=date(2025, 3, 31),
                disclosed_date=date(2025, 5, 10),
                disclosure_number="actual",
                revenue=1300,
                operating_income=85,
                net_income=-10,
                next_forecast_revenue=None,
                next_forecast_operating_income=None,
                next_forecast_net_income=None,
            ),
            *extra,
        ],
        schema=build_schema(),
    )
    conn = duckdb.connect()
    conn.register("financial_disclosure", table)
    return conn


def test_reusable_metric_comparisons_use_first_annual_release():
    with lake(
        disclosure(
            fiscal_year_end=date(2025, 3, 31),
            disclosed_date=date(2025, 2, 1),
            disclosure_number="quarter",
            fiscal_period_type="3Q",
            operating_income=82,
        ),
        disclosure(
            disclosed_date=date(2024, 5, 15),
            disclosure_number="correction",
            next_forecast_operating_income=90,
        ),
    ) as conn:
        forecast = read_inputs(conn, "9999.T", "operating_income", "forecast_variance")
        yoy = read_inputs(conn, "9999.T", "operating_income", "yoy")
        absolute = read_inputs(conn, "9999.T", "operating_income", "absolute")
    current = next(row for row in forecast if row["actual_disclosure_number"] == "actual")
    assert current["reference_value"] == 100
    assert current["observed_value"] == -15
    assert next(row for row in yoy if row["actual_disclosure_number"] == "actual")["observed_value"] == 6.25
    assert next(row for row in absolute if row["actual_disclosure_number"] == "actual")["observed_value"] == 85


def test_numeric_and_sign_transition_operators():
    with lake() as conn:
        variance = next(
            row
            for row in read_inputs(conn, "9999.T", "operating_income", "forecast_variance")
            if row["actual_disclosure_number"] == "actual"
        )
        sign = next(
            row
            for row in read_inputs(conn, "9999.T", "net_income", "sign_transition")
            if row["actual_disclosure_number"] == "actual"
        )
    assert evaluate(variance, "lte", Decimal("-10"), date(2025, 5, 11))["status"] == "fired"
    assert evaluate(variance, "lt", Decimal("-15"), date(2025, 5, 11))["status"] == "not_fired"
    assert evaluate(variance, "lte", Decimal("-15"), date(2025, 5, 11))["status"] == "fired"
    assert evaluate(variance, "gt", Decimal("-15"), date(2025, 5, 11))["status"] == "not_fired"
    assert evaluate(variance, "gte", Decimal("-15"), date(2025, 5, 11))["status"] == "fired"
    assert evaluate(variance, "lte", Decimal("-20"), date(2025, 5, 11))["status"] == "not_fired"
    assert evaluate(sign, "crosses_below", None, date(2025, 5, 11))["status"] == "fired"
    assert evaluate(sign, "crosses_above", None, date(2025, 5, 11))["status"] == "not_fired"
    assert evaluate(variance, "lte", Decimal("-10"), date(2025, 5, 10))["status"] == "unavailable"


def test_missing_reference_is_not_nonfire():
    with lake() as conn:
        first = next(
            row
            for row in read_inputs(conn, "9999.T", "operating_income", "yoy")
            if row["actual_disclosure_number"] == "forecast"
        )
    assert evaluate(first, "lte", Decimal("-10"), date(2025, 5, 11))["status"] == "unavailable"


def test_bigquery_fact_preserves_lineage_and_uses_bounded_parameterized_query():
    client = BigQueryStub(
        [
            {
                "comparison_key": "comparison-1",
                "target_key": "9999.T",
                "actual_disclosure_number": "actual",
                "prior_disclosure_number": "forecast",
                "observed_value": -20,
                "reference_value": 100,
                "current_value": 80,
                "input_status": "ok",
                "available_on": date(2025, 5, 10),
                "calculation_definition_version": "dbt-fct-metric-comparison-v1",
            }
        ]
    )
    rows = read_bigquery_inputs(
        project="invest-dwh",
        dataset="invest_marts",
        location="australia-southeast1",
        maximum_bytes_billed=1024,
        target_keys=["9999.T"],
        metric_key="operating_income",
        comparison_type="forecast_variance",
        as_of=date(2025, 5, 10),
        client=client,
    )
    assert rows[0]["comparison_key"] == "comparison-1"
    assert "security_key IN UNNEST(@target_keys)" in client.query_text
    assert client.job_config.maximum_bytes_billed == 1024
    # 複数の指標・比較軸をまとめて取れるよう配列で渡す。複合Ruleでメンバーが増えても
    # クエリ本数は変わらない。
    assert "metric_key IN UNNEST(@metric_keys)" in client.query_text
    assert "comparison_type IN UNNEST(@comparison_types)" in client.query_text
    assert {parameter.name for parameter in client.job_config.query_parameters} == {
        "target_keys",
        "metric_keys",
        "comparison_types",
        "as_of",
    }
    result = evaluate(rows[0], "lte", Decimal("-20"), date(2025, 5, 10))
    assert result["status"] == "fired"
    assert result["event_key"] == "comparison-1:actual:forecast"
    assert result["definition_version"] == "dbt-fct-metric-comparison-v1"


def test_full_replay_response_and_versioning(client, monkeypatch):
    conn = lake()
    monkeypatch.setattr(routes, "get_analytics_connection", lambda: conn)
    target = client.post(
        "/api/investment-targets/",
        json={
            "target_key": "9999.T",
            "target_name": "架空小売",
            "target_type": "individual_stock",
        },
    ).json()
    body = {
        "target_id": target["target_id"],
        "metric_key": "operating_income",
        "comparison_type": "forecast_variance",
        "operator": "lte",
        "threshold_value": -10,
        "rationale": "投資理由を再確認する",
    }
    assert client.get("/api/triggers/definitions").status_code == 200
    rule = client.post("/api/triggers/rules", json=body)
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["rule_id"]
    path = f"/api/triggers/rules/{rule_id}/evaluate"
    request = {"mode": "replay", "as_of_date": "2025-05-11"}
    assert client.post(path, json=request).json()["inserted"] == 2
    assert client.post(path, json=request).json()["inserted"] == 0
    events = client.get("/api/triggers/evaluations", params={"mode": ""}).json()
    event = next(item for item in events if item["status"] == "fired")
    assert event["mode"] == "replay"
    assert event["evidence"]["metric_key"] == "operating_income"
    responses = f"/api/triggers/evaluations/{event['evaluation_id']}/responses"
    assert (
        client.post(
            responses,
            json={
                "disposition": "overridden",
                "override_decision": "no_action",
                "rationale": "一時要因と考える",
                "next_review_on": "2025-06-01",
            },
        ).status_code
        == 201
    )
    saved_responses = client.get(responses).json()
    assert len(saved_responses) == 1
    assert saved_responses[0]["disposition"] == "overridden"
    assert saved_responses[0]["override_decision"] == "no_action"
    assert saved_responses[0]["next_review_on"] == "2025-06-01"
    assert (
        client.post(
            responses,
            json={"kind": "learning", "choice": "useful", "note": "確認の契機になった"},
        ).status_code
        == 422
    )
    newer = client.post(
        "/api/triggers/rules",
        json={**body, "supersedes_id": rule_id, "threshold_value": -20},
    )
    assert newer.status_code == 201, newer.text
    assert newer.json()["version"] == 2
    assert client.post(path, json=request).json()["inserted"] == 2
    conn.close()


def test_trigger_carries_decision_policy_and_response_confirms_or_overrides(client):
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "7777.T", "target_name": "架空企業D", "target_type": "individual_stock"},
    ).json()
    rule = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "yoy",
            "operator": "lte",
            "threshold_value": -30,
            "on_fire_decision": "sell",
            "rationale": "利益悪化時は売却方針を確認する",
        },
    )
    assert rule.status_code == 201, rule.text
    assert rule.json()["on_fire_decision"] == "sell"

    invalid_override = client.post(
        "/api/triggers/evaluations/999/responses",
        json={"disposition": "overridden", "rationale": "方針変更"},
    )
    assert invalid_override.status_code == 422


def test_route_evaluates_bigquery_fact_and_is_idempotent(client, monkeypatch):
    from app.config import settings

    target = client.post(
        "/api/investment-targets/",
        json={
            "target_key": "9831.T",
            "target_name": "架空小売B",
            "target_type": "individual_stock",
        },
    ).json()
    rule = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -20,
            "rationale": "期初想定から大きく外れたら再評価する",
        },
    ).json()
    fact = {
        "comparison_key": "comparison-9831-2026",
        "security_key": "9831.T",
        "target_key": "9831.T",
        "fiscal_year_end": date(2026, 3, 31),
        "actual_disclosed_date": date(2026, 5, 8),
        "available_on": date(2026, 5, 8),
        "actual_disclosure_number": "actual-2026",
        "prior_disclosure_number": "forecast-2025",
        "reporting_scope": "consolidated",
        "accounting_standard": "JGAAP",
        "metric_key": "operating_income",
        "comparison_type": "forecast_variance",
        "reference_value": 489,
        "current_value": 161.66,
        "observed_value": -66.94,
        "input_status": "ok",
        "raw_uri": "gs://example/raw.json.gz",
        "response_sha256": "response-hash",
        "source_record_hash": "record-hash",
        "calculation_definition_version": "dbt-fct-metric-comparison-v1",
    }
    monkeypatch.setattr(settings, "bigquery_project", "invest-dwh")
    monkeypatch.setattr(trigger_service, "read_bigquery_inputs", lambda **_: [fact])
    path = f"/api/triggers/rules/{rule['rule_id']}/evaluate"
    request = {"mode": "replay", "as_of_date": "2026-05-08"}
    first = client.post(path, json=request)
    second = client.post(path, json=request)
    assert first.status_code == 200
    assert first.json()["inserted"] == 1
    assert second.json()["inserted"] == 0
    evaluation = client.get("/api/triggers/evaluations", params={"mode": ""}).json()[0]
    assert evaluation["status"] == "fired"
    assert evaluation["definition_version"] == "dbt-fct-metric-comparison-v1"
    assert evaluation["evidence"]["raw_uri"] == "gs://example/raw.json.gz"


def test_watchlist_scope_expands_to_each_current_member(client, monkeypatch):
    from app.config import settings

    targets = [
        client.post(
            "/api/investment-targets/",
            json={"target_key": key, "target_name": name, "target_type": "individual_stock"},
        ).json()
        for key, name in (("1111.T", "架空企業A"), ("2222.T", "架空企業B"))
    ]
    rule_response = client.post(
        "/api/triggers/rules",
        json={
            "scope_type": "watchlist",
            "watchlist_status": "monitoring",
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -20,
            "rationale": "監視中の銘柄を同じ基準で確認する",
        },
    )
    assert rule_response.status_code == 201, rule_response.text
    rule = rule_response.json()
    assert rule["scope_type"] == "watchlist"
    assert rule["scope_label"] == "ウォッチリスト: monitoring"

    def facts(**kwargs):
        assert set(kwargs["target_keys"]) == {target["target_key"] for target in targets}
        return [
            {
                "comparison_key": f"comparison-{target['target_key']}",
                "target_key": target["target_key"],
                "actual_disclosure_number": "actual",
                "prior_disclosure_number": "forecast",
                "available_on": date(2026, 5, 8),
                "actual_disclosed_date": date(2026, 5, 8),
                "observed_value": -25,
                "reference_value": 100,
                "current_value": 75,
                "input_status": "ok",
                "calculation_definition_version": "dbt-fct-metric-comparison-v1",
            }
            for target in targets
        ]

    monkeypatch.setattr(settings, "bigquery_project", "invest-dwh")
    monkeypatch.setattr(trigger_service, "read_bigquery_inputs", facts)
    result = client.post(
        f"/api/triggers/rules/{rule['rule_id']}/evaluate",
        json={"mode": "replay", "as_of_date": "2026-05-08"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["inserted"] == 2
    evaluations = client.get("/api/triggers/evaluations", params={"mode": ""}).json()
    assert {row["target_key"] for row in evaluations} == {"1111.T", "2222.T"}
    assert all(row["evidence"]["trigger_scope_type"] == "watchlist" for row in evaluations)


def test_theme_and_mandate_scopes_expand_their_current_members(client, monkeypatch):
    from app.config import settings

    today = date.today()
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "3333.T", "target_name": "架空企業C", "target_type": "individual_stock"},
    ).json()
    theme = client.post(
        "/api/themes/",
        json={"theme_key": "retail-test", "theme_name": "小売テスト", "description": "scope test"},
    ).json()
    assert (
        client.post(
            f"/api/relationships/themes/{theme['theme_id']}/investment-targets",
            json={"target_id": target["target_id"]},
        ).status_code
        == 201
    )
    mandate = client.post(
        "/api/mandates/",
        json={"mandate_name": "成長枠テスト", "status": "active", "purpose": "scope test"},
    ).json()
    assert (
        client.put(
            f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target['target_id']}",
            json={"target_id": target["target_id"], "status": "active", "target_weight": 0.1},
        ).status_code
        == 200
    )

    rules = []
    for scope in (
        {"scope_type": "theme", "theme_id": theme["theme_id"]},
        {"scope_type": "mandate", "mandate_id": mandate["mandate_id"]},
    ):
        response = client.post(
            "/api/triggers/rules",
            json={
                **scope,
                "metric_key": "operating_income",
                "comparison_type": "forecast_variance",
                "operator": "lte",
                "threshold_value": -20,
                "rationale": "集合へ共通ルールを適用する",
            },
        )
        assert response.status_code == 201, response.text
        rules.append(response.json())
    assert [rule["scope_label"] for rule in rules] == ["小売テスト", "成長枠テスト"]

    def facts(**kwargs):
        assert kwargs["target_keys"] == ["3333.T"]
        return [
            {
                "comparison_key": "comparison-3333",
                "target_key": "3333.T",
                "actual_disclosure_number": "actual",
                "prior_disclosure_number": "forecast",
                "available_on": today,
                "actual_disclosed_date": today,
                "observed_value": -25,
                "reference_value": 100,
                "current_value": 75,
                "input_status": "ok",
                "calculation_definition_version": "dbt-fct-metric-comparison-v1",
            }
        ]

    monkeypatch.setattr(settings, "bigquery_project", "invest-dwh")
    monkeypatch.setattr(trigger_service, "read_bigquery_inputs", facts)
    for rule in rules:
        response = client.post(
            f"/api/triggers/rules/{rule['rule_id']}/evaluate",
            json={"mode": "replay", "as_of_date": today.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["inserted"] == 1


def test_public_demo_rejects_rule_creation(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "public_demo_write_enabled", True)
    monkeypatch.setattr(settings, "database_environment_override", "demo")
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql://invest_demo_writer:unused@localhost/invest_test",
    )
    monkeypatch.setattr(settings, "management_api_enabled", False)
    monkeypatch.setattr(settings, "database_read_only", False)
    assert client.post("/api/triggers/rules", json={}).status_code == 405


def test_disabled_rule_is_hidden_but_recoverable_without_losing_history(client, monkeypatch):
    """停止したRuleは一覧から外れ、再開でき、過去の判定は残る。

    物理削除しないのは `trigger_evaluation` が `rule_version_id` を参照するためで、
    一覧から外すのは表示の都合である。停止を終了として扱うと、同じ条件へ戻すのに
    新規作成しかなく、`rule_key` が変わって履歴が切れる。
    """
    conn = lake()
    monkeypatch.setattr(routes, "get_analytics_connection", lambda: conn)
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "9999.T", "target_name": "架空停止", "target_type": "individual_stock"},
    ).json()
    rule_id = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -10,
            "rationale": "停止と再開を確かめる",
        },
    ).json()["rule_id"]
    evaluate_path = f"/api/triggers/rules/{rule_id}/evaluate"
    assert client.post(evaluate_path, json={"mode": "replay", "as_of_date": "2025-05-11"}).json()["inserted"] > 0
    before = len(client.get("/api/triggers/evaluations", params={"mode": ""}).json())

    assert client.post(f"/api/triggers/rules/{rule_id}/disable").status_code == 204

    ids = [item["rule_id"] for item in client.get("/api/triggers/rules").json()]
    assert rule_id not in ids, "停止済みが既定の一覧に残っている"
    inactive = client.get("/api/triggers/rules", params={"include_inactive": "true"}).json()
    stopped = next(item for item in inactive if item["rule_id"] == rule_id)
    assert stopped["is_active"] is False
    after = client.get("/api/triggers/evaluations", params={"mode": ""}).json()
    assert len(after) == before, "停止で過去の判定が消えた"

    assert client.post(f"/api/triggers/rules/{rule_id}/enable").status_code == 204
    revived = next(item for item in client.get("/api/triggers/rules").json() if item["rule_id"] == rule_id)
    assert revived["is_active"] is True
    assert revived["version"] == stopped["version"], "再開でversionが進んだ"


def test_enable_rejects_unknown_rule(client):
    assert client.post("/api/triggers/rules/999999/enable").status_code == 404


def test_rule_can_resolve_an_unregistered_security_from_the_catalog(client, monkeypatch):
    """個別Ruleの作成時に全銘柄マスタからinvestment_targetを遅延登録する。"""

    class SecuritySourceStub:
        def __init__(self, connection):
            pass

        def find(self, security_key):
            assert security_key == "catalog-security"
            return {
                "target_key": "7777.T",
                "company_name": "全銘柄マスタ企業",
                "product_category_code": "011",
                "market_name": "Prime",
            }

    monkeypatch.setattr(routes, "get_analytics_connection", lambda: object())
    monkeypatch.setattr(routes, "ParquetSecuritySource", SecuritySourceStub)
    response = client.post(
        "/api/triggers/rules",
        json={
            "scope_type": "investment_target",
            "target_security_key": "catalog-security",
            "metric_key": "operating_income",
            "comparison_type": "yoy",
            "operator": "lte",
            "threshold_value": -20,
            "rationale": "全銘柄から選ぶ",
        },
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    assert rule["target_key"] == "7777.T"
    assert rule["target_name"] == "全銘柄マスタ企業"


def test_stopped_rule_can_be_fully_deleted_with_its_history(client, monkeypatch):
    """完全削除は停止後だけ許可し、判定・対応履歴を孤立させない。"""
    conn = lake()
    monkeypatch.setattr(routes, "get_analytics_connection", lambda: conn)
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "9999.T", "target_name": "完全削除企業", "target_type": "individual_stock"},
    ).json()
    rule = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -10,
            "rationale": "削除動作を確かめる",
        },
    ).json()
    rule_id = rule["rule_id"]
    assert client.delete(f"/api/triggers/rules/{rule_id}").status_code == 409

    client.post(
        f"/api/triggers/rules/{rule_id}/evaluate",
        json={"mode": "replay", "as_of_date": "2025-05-11"},
    )
    evaluation = next(
        item
        for item in client.get("/api/triggers/evaluations", params={"mode": ""}).json()
        if item["status"] == "fired"
    )
    assert (
        client.post(
            f"/api/triggers/evaluations/{evaluation['evaluation_id']}/responses",
            json={
                "disposition": "accepted",
                "rationale": "削除対象のテスト対応",
                "next_review_on": None,
            },
        ).status_code
        == 201
    )
    assert client.post(f"/api/triggers/rules/{rule_id}/disable").status_code == 204
    stopped = next(
        item for item in client.get("/api/triggers/rules?include_inactive=true").json() if item["rule_id"] == rule_id
    )
    assert stopped["evaluation_count"] == 2
    assert stopped["response_count"] == 1

    deleted = client.delete(f"/api/triggers/rules/{rule_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "rule_id": rule_id,
        "deleted_versions": 1,
        "deleted_evaluations": 2,
        "deleted_responses": 1,
    }
    assert all(item["rule_id"] != rule_id for item in client.get("/api/triggers/rules?include_inactive=true").json())
    assert all(
        item["rule_id"] != rule_id for item in client.get("/api/triggers/evaluations", params={"mode": ""}).json()
    )


def test_zero_result_messages_distinguish_their_cause(client, monkeypatch):
    """0件の理由を区別する。

    「対象開示なし」とだけ返すと、指標の開示が無いのか、基準日より後なのか、
    実運用でRule登録前の開示しか無いのかを切り分けられない。
    """
    conn = lake()
    monkeypatch.setattr(routes, "get_analytics_connection", lambda: conn)
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "9999.T", "target_name": "架空小売", "target_type": "individual_stock"},
    ).json()
    rule_id = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -10,
            "rationale": "0件の理由を確かめる",
        },
    ).json()["rule_id"]
    path = f"/api/triggers/rules/{rule_id}/evaluate"

    # 開示はあるが、基準日がそれより前。
    early = client.post(path, json={"mode": "replay", "as_of_date": "2020-01-01"}).json()
    assert early["evaluated"] == 0
    assert early["message"] == "評価基準日の時点ではまだ公表されていません"

    # 実運用はRule登録後の開示だけを見るので、既存の開示では0件になる。
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    live = client.post(path, json={"mode": "live", "as_of_date": today}).json()
    assert live["evaluated"] == 0
    assert live["message"] == "ルール登録後に公表された開示がまだありません"

    # 開示そのものを取得できなかった場合。fixtureのlakeは全組み合わせに行があるため、
    # 取得0件の分岐だけは関数へ直接渡して確かめる。
    assert routes._evaluation_message(0, 0, "replay") == "この指標・比較の開示データがありません"


def test_work_queue_excludes_replay_and_future_deferrals(client, monkeypatch):
    """判定一覧は作業キューとして振る舞う。

    過去再生はRuleの校正用で、人間が対応すべき案件ではない。同じ一覧へ混ぜると、
    動作確認のために押した結果が作業キューを埋める。再確認日が未来の保留も、
    今やることではないので既定から外す。
    """
    conn = lake()
    monkeypatch.setattr(routes, "get_analytics_connection", lambda: conn)
    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "9999.T", "target_name": "架空小売", "target_type": "individual_stock"},
    ).json()
    rule_id = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "metric_key": "operating_income",
            "comparison_type": "forecast_variance",
            "operator": "lte",
            "threshold_value": -10,
            "rationale": "作業キューの挙動を確かめる",
        },
    ).json()["rule_id"]
    client.post(
        f"/api/triggers/rules/{rule_id}/evaluate",
        json={"mode": "replay", "as_of_date": "2025-05-11"},
    )

    assert client.get("/api/triggers/evaluations").json() == [], "過去再生が一覧へ出ている"
    # 検証用に mode を空にすれば読める。保存自体は消していない。
    assert client.get("/api/triggers/evaluations", params={"mode": ""}).json() != []
    assert client.get("/api/triggers/evaluations", params={"work_state": "unknown"}).status_code == 422


def test_deferred_returns_to_the_queue_when_its_review_date_arrives(client):
    """再確認日が到来した保留は要対応へ戻る。"""
    repo = routes.TriggerRepository
    source = inspect.getsource(repo.evaluations)
    assert "next_review_on <= CURRENT_DATE" in source
    assert "latest_response_disposition = 'deferred'" in source
    assert "next_review_on > CURRENT_DATE" in source


def test_work_queue_states_only_include_fired_evaluations():
    """条件非該当・判定不能は履歴には残すが、人間の対応キューへ入れない。"""
    source = inspect.getsource(routes.TriggerRepository.evaluations)

    for state in ("unhandled", "actionable", "deferred", "settled"):
        assert f'"{state}"' in source
    assert source.count("status = 'fired'") >= 4


def test_multiple_comparisons_share_a_single_query():
    """複数の指標・比較軸を1回の取得でまとめる。

    BigQueryは列指向なので`WHERE`で行を絞ってもスキャン量は変わらない。実測では
    `metric_key`と`comparison_type`で絞っても全件でも同じ14,022,305バイトだった。
    条件ごとにクエリを投げるのは同じデータを読み直しているだけで、複合Ruleでは
    メンバー数ぶん課金が増える。
    """
    calls: list[dict[str, Any]] = []

    def fake_read(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [
            {"metric_key": metric, "comparison_type": comparison, "target_key": "9999.T"}
            for metric in kwargs["metric_key"]
            for comparison in kwargs["comparison_type"]
        ]

    settings_stub = SimpleNamespace(
        bigquery_project="invest-dwh",
        bigquery_marts_dataset="invest_marts",
        bigquery_location="australia-southeast1",
        bigquery_maximum_bytes_billed=1024,
    )
    with patch.object(trigger_service, "read_bigquery_inputs", fake_read):
        facts = trigger_service.fetch_comparison_facts(
            target_keys=["9999.T"],
            comparisons=[
                ("operating_income", "forecast_variance"),
                ("operating_income", "yoy"),
            ],
            as_of=date(2026, 5, 8),
            settings=settings_stub,
            connection=None,
        )

    assert len(calls) == 1, "比較軸ごとにクエリを投げている"
    assert sorted(calls[0]["metric_key"]) == ["operating_income"]
    assert sorted(calls[0]["comparison_type"]) == ["forecast_variance", "yoy"]
    assert len(facts.rows_for("operating_income", "forecast_variance")) == 1
    assert len(facts.rows_for("operating_income", "yoy")) == 1


def test_multi_condition_rule_aggregates_all_and_keeps_one_work_item(client, monkeypatch):
    """複数Conditionは同じ開示イベントについて1件のRule評価へ集約する。"""
    from app.config import settings

    target = client.post(
        "/api/investment-targets/",
        json={"target_key": "4444.T", "target_name": "複合条件企業", "target_type": "individual_stock"},
    ).json()
    response = client.post(
        "/api/triggers/rules",
        json={
            "target_id": target["target_id"],
            "logic_operator": "all",
            "conditions": [
                {
                    "metric_key": "operating_income",
                    "comparison_type": "forecast_variance",
                    "operator": "lte",
                    "threshold_value": -20,
                },
                {
                    "metric_key": "operating_income",
                    "comparison_type": "yoy",
                    "operator": "lte",
                    "threshold_value": -20,
                },
            ],
            "rationale": "予想比と前年比の双方で悪化したら調査する",
        },
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    assert rule["logic_operator"] == "all"
    assert len(rule["conditions"]) == 2

    def facts(**kwargs):
        assert kwargs["metric_key"] == ["operating_income"]
        assert kwargs["comparison_type"] == ["forecast_variance", "yoy"]
        return [
            {
                "comparison_key": f"4444-{comparison}",
                "target_key": "4444.T",
                "fiscal_year_end": date(2026, 3, 31),
                "actual_disclosure_number": "actual-2026",
                "actual_disclosed_date": date(2026, 5, 8),
                "available_on": date(2026, 5, 8),
                "reporting_scope": "consolidated",
                "accounting_standard": "JGAAP",
                "metric_key": "operating_income",
                "comparison_type": comparison,
                "reference_value": 100,
                "current_value": value,
                "observed_value": value - 100,
                "input_status": "ok",
                "calculation_definition_version": "dbt-fct-metric-comparison-v1",
            }
            for comparison, value in (("forecast_variance", 70), ("yoy", 75))
        ]

    monkeypatch.setattr(settings, "bigquery_project", "invest-dwh")
    monkeypatch.setattr(trigger_service, "read_bigquery_inputs", facts)
    result = client.post(
        f"/api/triggers/rules/{rule['rule_id']}/evaluate",
        json={"mode": "replay", "as_of_date": "2026-05-08"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["inserted"] == 1
    evaluations = client.get("/api/triggers/evaluations", params={"mode": ""}).json()
    assert len(evaluations) == 1
    assert evaluations[0]["status"] == "fired"
    assert [item["status"] for item in evaluations[0]["evidence"]["conditions"]] == ["fired", "fired"]


def test_multi_condition_three_valued_logic():
    condition = {
        "metric_key": "operating_income",
        "comparison_type": "yoy",
        "operator": "lte",
        "threshold_value": Decimal("-20"),
    }
    fired_row = {
        "target_key": "1.T",
        "available_on": date(2026, 1, 1),
        "input_status": "ok",
        "metric_key": "operating_income",
        "comparison_type": "yoy",
        "observed_value": -25,
        "reference_value": 100,
        "current_value": 75,
    }
    missing_condition = {**condition, "comparison_type": "forecast_variance"}
    all_result = trigger_service.evaluate_conditions(
        [fired_row], [condition, missing_condition], "all", date(2026, 1, 1)
    )
    any_result = trigger_service.evaluate_conditions(
        [fired_row], [condition, missing_condition], "any", date(2026, 1, 1)
    )
    assert all_result["status"] == "unavailable"
    assert any_result["status"] == "fired"
