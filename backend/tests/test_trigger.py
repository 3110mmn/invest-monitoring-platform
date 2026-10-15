from datetime import date
from decimal import Decimal

import duckdb
import pyarrow as pa

from app.routers.domain import triggers as routes
from app.services.trigger import evaluate, read_inputs
from scripts.build_financial_parquet import build_schema


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
    events = client.get("/api/triggers/evaluations").json()
    event = next(item for item in events if item["status"] == "fired")
    assert event["mode"] == "replay"
    assert event["evidence"]["metric_key"] == "operating_income"
    responses = f"/api/triggers/evaluations/{event['evaluation_id']}/responses"
    assert (
        client.post(
            responses,
            json={
                "response_type": "continue",
                "rationale": "一時要因と考える",
                "next_review_on": "2025-06-01",
            },
        ).status_code
        == 201
    )
    saved_responses = client.get(responses).json()
    assert len(saved_responses) == 1
    assert saved_responses[0]["response_type"] == "continue"
    assert saved_responses[0]["next_review_on"] == "2025-06-01"
    assert client.post(
        responses,
        json={"kind": "learning", "choice": "useful", "note": "確認の契機になった"},
    ).status_code == 422
    newer = client.post(
        "/api/triggers/rules",
        json={**body, "supersedes_id": rule_id, "threshold_value": -20},
    )
    assert newer.status_code == 201, newer.text
    assert newer.json()["version"] == 2
    assert client.post(path, json=request).json()["inserted"] == 2
    conn.close()


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
