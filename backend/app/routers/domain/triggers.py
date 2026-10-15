"""Generic operational trigger rules, evaluations and human responses."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from app.analytics_connection import get_analytics_connection
from app.database import Connection, get_db
from app.models.trigger import (
    EvaluateRequest,
    EvaluationRead,
    ResponseCreate,
    ResponseRead,
    RuleCreate,
    RuleRead,
)
from app.repositories.trigger_repository import TriggerRepository
from app.services.trigger import evaluate, read_inputs

router = APIRouter(prefix="/triggers", tags=["triggers"])


def _repo(conn: Connection = Depends(get_db)) -> TriggerRepository:
    return TriggerRepository(conn)


@router.get("/definitions")
def definitions():
    """Return the constrained vocabulary accepted by the generic rule API."""
    return {
        "definitions": [
            {
                "key": "financial_metric_comparison",
                "label": "財務指標の比較",
                "metrics": [
                    {"key": "revenue", "label": "売上高"},
                    {"key": "operating_income", "label": "営業利益"},
                    {"key": "net_income", "label": "純利益"},
                ],
                "comparisons": [
                    {
                        "key": "forecast_variance",
                        "label": "期初予想比",
                        "operators": ["lte", "gte"],
                    },
                    {
                        "key": "yoy",
                        "label": "前年同期比",
                        "operators": ["lte", "gte"],
                    },
                    {
                        "key": "absolute",
                        "label": "実績値",
                        "operators": ["lte", "gte"],
                    },
                    {
                        "key": "sign_transition",
                        "label": "符号転換",
                        "operators": ["crosses_below", "crosses_above"],
                    },
                ],
            }
        ]
    }


@router.get("/rules", response_model=list[RuleRead])
def rules(repo: TriggerRepository = Depends(_repo)):
    return repo.rules()


@router.post("/rules", response_model=RuleRead, status_code=201)
def create_rule(body: RuleCreate, repo: TriggerRepository = Depends(_repo)):
    if not repo.execute_single("SELECT target_id FROM investment_target WHERE target_id = ?", (body.target_id,)):
        raise HTTPException(404, "投資対象がありません")
    try:
        rule_id = repo.create_rule(body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return repo.rule(rule_id)


@router.post("/rules/{rule_id}/disable", status_code=204)
def disable(rule_id: int, repo: TriggerRepository = Depends(_repo)):
    if repo.rule(rule_id, lock=True) is None:
        raise HTTPException(404, "ルールがありません")
    repo.execute_write("UPDATE trigger_rule SET is_active = FALSE WHERE rule_id = ?", (rule_id,))


@router.post("/rules/{rule_id}/evaluate")
def evaluate_rule(rule_id: int, body: EvaluateRequest, repo: TriggerRepository = Depends(_repo)):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    if body.as_of_date > today or (body.mode == "live" and body.as_of_date != today):
        raise HTTPException(422, "未来日は指定できません。実運用は本日のみです")
    rule = repo.rule(rule_id, lock=True)
    if rule is None:
        raise HTTPException(404, "ルールがありません")
    if not rule["is_active"]:
        raise HTTPException(409, "停止済みのルールです")
    connection = get_analytics_connection()
    if connection is None:
        raise HTTPException(503, "分析データへ接続できません")
    rows = read_inputs(
        connection,
        rule["target_key"],
        rule["metric_key"],
        rule["comparison_type"],
    )
    start = rule["created_at"].astimezone(ZoneInfo("Asia/Tokyo")).date()
    rows = [
        row
        for row in rows
        if row["available_on"] <= body.as_of_date and (body.mode == "replay" or row["actual_disclosed_date"] > start)
    ]
    inserted = 0
    for row in rows:
        result = evaluate(row, rule["operator"], rule["threshold_value"], body.as_of_date)
        inserted += repo.save_evaluation(rule["rule_version_id"], body.mode, body.as_of_date, result)
    return {
        "evaluated": len(rows),
        "inserted": inserted,
        "message": "対象開示なし（データ不足または対象期間外）" if not rows else "評価完了",
    }


@router.get("/evaluations", response_model=list[EvaluationRead])
def evaluations(rule_id: int | None = None, repo: TriggerRepository = Depends(_repo)):
    return repo.evaluations(rule_id)


@router.get("/evaluations/{evaluation_id}/responses", response_model=list[ResponseRead])
def responses(evaluation_id: int, repo: TriggerRepository = Depends(_repo)):
    return repo.responses(evaluation_id)


@router.post(
    "/evaluations/{evaluation_id}/responses",
    response_model=ResponseRead,
    status_code=201,
)
def respond(
    evaluation_id: int,
    body: ResponseCreate,
    repo: TriggerRepository = Depends(_repo),
):
    record = repo.execute_single(
        "SELECT status FROM trigger_evaluation WHERE evaluation_id = ?",
        (evaluation_id,),
    )
    if record is None:
        raise HTTPException(404, "評価がありません")
    if record["status"] != "fired":
        raise HTTPException(409, "発火した評価への対応を記録してください")
    response_id = repo.respond(evaluation_id, body)
    return repo.execute_single("SELECT * FROM trigger_response WHERE response_id = ?", (response_id,))
