"""Generic operational trigger rules, evaluations and human responses."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from app.analytics_connection import get_analytics_connection
from app.config import settings
from app.database import Connection, get_db
from app.etl.normalizers import PRODUCT_TYPES
from app.models.trigger import (
    EvaluateRequest,
    EvaluationRead,
    ResponseCreate,
    ResponseRead,
    RuleCreate,
    RuleDeleteRead,
    RuleRead,
)
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.security_source import ParquetSecuritySource
from app.repositories.trigger_repository import TriggerRepository
from app.services.trigger_operations import (
    ComparisonFactsUnavailable,
    RuleInactiveError,
    RuleNotFoundError,
)
from app.services.trigger_operations import (
    evaluate_rule as run_rule_evaluation,
)

router = APIRouter(prefix="/triggers", tags=["triggers"])


def _repo(conn: Connection = Depends(get_db)) -> TriggerRepository:
    return TriggerRepository(conn)


def _resolve_target_security(security_key: str, conn: Connection) -> int:
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(503, "Analytics layer is not configured (PARQUET_LAKE)")
    security = ParquetSecuritySource(analytics).find(security_key)
    if security is None or not security["target_key"]:
        raise HTTPException(404, "投資対象として登録できる銘柄がありません")
    return InvestmentTargetRepository(conn).ensure_from_security(
        {
            "target_key": security["target_key"],
            "target_name": security["company_name"],
            "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
            "market": security["market_name"],
            "currency": "JPY",
        }
    )


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
                        "operators": ["lt", "lte", "gt", "gte"],
                    },
                    {
                        "key": "yoy",
                        "label": "前年同期比",
                        "operators": ["lt", "lte", "gt", "gte"],
                    },
                    {
                        "key": "absolute",
                        "label": "実績値",
                        "operators": ["lt", "lte", "gt", "gte"],
                    },
                    {
                        "key": "sign_transition",
                        "label": "状態変化（黒字・赤字）",
                        "operators": ["crosses_below", "crosses_above"],
                    },
                ],
            }
        ]
    }


@router.get("/rules", response_model=list[RuleRead])
def rules(include_inactive: bool = False, repo: TriggerRepository = Depends(_repo)):
    return repo.rules(include_inactive=include_inactive)


@router.post("/rules", response_model=RuleRead, status_code=201)
def create_rule(body: RuleCreate, repo: TriggerRepository = Depends(_repo)):
    if body.target_security_key:
        body.target_id = _resolve_target_security(body.target_security_key, repo.conn)
        body.target_security_key = None
    if not repo.scope_exists(body):
        raise HTTPException(404, "適用対象がありません")
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


@router.post("/rules/{rule_id}/enable", status_code=204)
def enable(rule_id: int, repo: TriggerRepository = Depends(_repo)):
    """停止したRuleを再び有効にする。

    停止は終了ではなく中断として扱う。決算期だけ見る、今期は見送る等の運用があり、
    同じ条件へ戻すために新規作成させると`rule_key`が変わって履歴が切れる。

    **過去の判定には影響しない。** `is_active`は`trigger_rule`側の列で、
    `trigger_evaluation`が参照するのは`rule_version_id`である。
    """
    if repo.rule(rule_id, lock=True) is None:
        raise HTTPException(404, "ルールがありません")
    repo.execute_write("UPDATE trigger_rule SET is_active = TRUE WHERE rule_id = ?", (rule_id,))


@router.delete("/rules/{rule_id}", response_model=RuleDeleteRead)
def delete_rule(rule_id: int, repo: TriggerRepository = Depends(_repo)):
    """停止済みRuleをVersion・判定・対応履歴ごと完全削除する。"""
    try:
        return repo.delete_rule(rule_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _evaluation_message(fetched: int, evaluated: int, mode: str) -> str:
    """0件だった理由を区別して返す。

    「対象開示なし」とだけ返すと、この指標の開示が無いのか、基準日より後なのか、
    実運用でRule登録前の開示しか無いのかを利用者が切り分けられない。
    """
    if evaluated:
        return "評価完了"
    if not fetched:
        return "この指標・比較の開示データがありません"
    if mode == "live":
        return "ルール登録後に公表された開示がまだありません"
    return "評価基準日の時点ではまだ公表されていません"


@router.post("/rules/{rule_id}/evaluate")
def evaluate_rule(rule_id: int, body: EvaluateRequest, repo: TriggerRepository = Depends(_repo)):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    if body.as_of_date > today or (body.mode == "live" and body.as_of_date != today):
        raise HTTPException(422, "未来日は指定できません。実運用は本日のみです")
    try:
        summary = run_rule_evaluation(
            repo,
            rule_id=rule_id,
            as_of=body.as_of_date,
            mode=body.mode,
            settings=settings,
            analytics_connection=get_analytics_connection(),
        )
    except RuleNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuleInactiveError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ComparisonFactsUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if summary.target_count == 0:
        message = "適用範囲に投資対象がありません"
    else:
        message = _evaluation_message(summary.fetched, summary.evaluated, body.mode)
    return {
        "evaluated": summary.evaluated,
        "inserted": summary.inserted,
        "message": message,
    }


@router.get("/evaluations", response_model=list[EvaluationRead])
def evaluations(
    rule_id: int | None = None,
    work_state: str | None = None,
    mode: str | None = "live",
    repo: TriggerRepository = Depends(_repo),
):
    """既定では実運用の判定だけを返す。過去再生は作業キューへ出さない。

    `work_state` は `unhandled` / `actionable` / `deferred` / `settled`。
    `mode` を空にすると過去再生も含める（検証用）。
    """
    try:
        return repo.evaluations(rule_id, work_state=work_state, mode=mode or None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
