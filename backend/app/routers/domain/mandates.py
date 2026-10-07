"""Capital allocation mandate API."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.analytics_connection import get_analytics_connection
from app.config import settings
from app.database import Connection, get_db
from app.etl.normalizers import PRODUCT_TYPES
from app.models.mandate import (
    CapitalBudgetRead,
    CapitalBudgetUpdate,
    MandateAssignmentRead,
    MandateAssignmentUpsert,
    MandateCreate,
    MandateDetail,
    MandateRead,
    MandateReviewItem,
    MandateSecurityAssignment,
    MandateUpdate,
)
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.mandate_repository import MandateRepository
from app.repositories.security_source import ParquetSecuritySource

router = APIRouter(prefix="/mandates", tags=["capital-allocation-mandates"])


def _repo(conn: Connection = Depends(get_db)) -> MandateRepository:
    return MandateRepository(conn)


def _detail(repo: MandateRepository, mandate_id: int) -> dict:
    mandate = repo.find_current(mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")
    mandate["assignments"] = repo.list_assignments(mandate["mandate_version_id"])
    return mandate


def _require_public_demo_mandate(repo: MandateRepository, mandate_id: int) -> None:
    """Public visitors may mutate only unexpired mandates created in demo mode."""
    if settings.public_demo_write_enabled and not repo.is_active_public_demo_mandate(mandate_id):
        raise HTTPException(status_code=404, detail="Public demo mandate not found")


def _resolve_benchmark_security(security_key: str, conn: Connection) -> int:
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(status_code=503, detail="Analytics layer is not configured (PARQUET_LAKE)")
    security = ParquetSecuritySource(analytics).find(security_key)
    if security is None or not security["target_key"]:
        raise HTTPException(status_code=404, detail="Benchmark security is not assignable")
    return InvestmentTargetRepository(conn).ensure_from_security({
        "target_key": security["target_key"],
        "target_name": security["company_name"],
        "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
        "market": security["market_name"],
        "currency": "JPY",
    })


@router.get("/", response_model=list[MandateRead])
def list_mandates(repo: MandateRepository = Depends(_repo)):
    return repo.find_all()


@router.get("/capital-budget", response_model=CapitalBudgetRead | None)
def get_capital_budget(repo: MandateRepository = Depends(_repo)):
    return repo.get_capital_budget()


@router.get("/review-queue", response_model=list[MandateReviewItem])
def get_review_queue(repo: MandateRepository = Depends(_repo)):
    return repo.list_review_items()


@router.put("/capital-budget", response_model=CapitalBudgetRead)
def set_capital_budget(body: CapitalBudgetUpdate, repo: MandateRepository = Depends(_repo)):
    return repo.set_capital_budget(body.model_dump())


@router.post("/", response_model=MandateDetail, status_code=201)
def create_mandate(body: MandateCreate, repo: MandateRepository = Depends(_repo)):
    data = body.model_dump()
    security_key = data.pop("benchmark_security_key", None)
    if settings.public_demo_write_enabled and security_key:
        raise HTTPException(
            status_code=422,
            detail="The public demo can only use benchmarks already in the demo database",
        )
    if security_key:
        data["benchmark_target_id"] = _resolve_benchmark_security(security_key, repo.conn)
    demo_expires_at = None
    if settings.public_demo_write_enabled:
        # Public demo entries are disposable, and never become active investments.
        data["status"] = "draft"
        demo_expires_at = datetime.now(UTC) + timedelta(hours=settings.public_demo_mandate_ttl_hours)
    try:
        mandate_id = repo.create(data, demo_expires_at=demo_expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail(repo, mandate_id)


@router.get("/{mandate_id}", response_model=MandateDetail)
def get_mandate(mandate_id: int, repo: MandateRepository = Depends(_repo)):
    return _detail(repo, mandate_id)


@router.patch("/{mandate_id}", response_model=MandateDetail)
def update_mandate(mandate_id: int, body: MandateUpdate, repo: MandateRepository = Depends(_repo)):
    _require_public_demo_mandate(repo, mandate_id)
    changes = body.model_dump(exclude_unset=True)
    if settings.public_demo_write_enabled:
        if changes.get("benchmark_security_key"):
            raise HTTPException(
                status_code=422,
                detail="The public demo can only use benchmarks already in the demo database",
            )
        if changes.get("status", "draft") != "draft":
            raise HTTPException(status_code=422, detail="Public demo mandates must remain drafts")
    if "benchmark_security_key" in body.model_fields_set:
        security_key = changes.pop("benchmark_security_key")
        if security_key:
            changes["benchmark_target_id"] = _resolve_benchmark_security(security_key, repo.conn)
    try:
        updated = repo.create_version(mandate_id, changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")
    return _detail(repo, mandate_id)


@router.delete("/{mandate_id}", status_code=204)
def delete_mandate(mandate_id: int, repo: MandateRepository = Depends(_repo)):
    _require_public_demo_mandate(repo, mandate_id)
    if not repo.delete(mandate_id):
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")


@router.put("/{mandate_id}/investment-targets/{target_id}", response_model=list[MandateAssignmentRead])
def upsert_mandate_target(
    mandate_id: int,
    target_id: int,
    body: MandateAssignmentUpsert,
    repo: MandateRepository = Depends(_repo),
):
    if target_id != body.target_id:
        raise HTTPException(status_code=422, detail="target_id does not match request body")
    mandate = repo.find_current(mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")
    _require_public_demo_mandate(repo, mandate_id)
    if not repo.target_exists(target_id):
        raise HTTPException(status_code=404, detail="Investment Target not found")
    if settings.public_demo_write_enabled:
        repo.lock_public_demo_assignments()
    if (
        settings.public_demo_write_enabled
        and not repo.has_current_assignment(mandate["mandate_version_id"], target_id)
        and repo.count_current_assignments(mandate["mandate_version_id"])
            >= settings.public_demo_max_assignments_per_mandate
    ):
        raise HTTPException(status_code=422, detail="Public demo assignment limit reached")
    try:
        repo.upsert_assignment(mandate["mandate_version_id"], body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return repo.list_assignments(mandate["mandate_version_id"])


@router.put("/{mandate_id}/securities/{security_key}", response_model=list[MandateAssignmentRead])
def assign_security_from_catalog(
    mandate_id: int,
    security_key: str,
    body: MandateSecurityAssignment,
    conn: Connection = Depends(get_db),
):
    """Parquetの銘柄を投資枠へ採用する。Watchlistには追加しない。"""
    repo = MandateRepository(conn)
    mandate = repo.find_current(mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(status_code=503, detail="Analytics layer is not configured (PARQUET_LAKE)")
    security = ParquetSecuritySource(analytics).find(security_key)
    if security is None or not security["target_key"]:
        raise HTTPException(status_code=404, detail="Security is not assignable")
    targets = InvestmentTargetRepository(conn)
    target_id = targets.ensure_from_security({
        "target_key": security["target_key"],
        "target_name": security["company_name"],
        "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
        "market": security["market_name"],
        "currency": "JPY",
    })
    try:
        repo.upsert_assignment(mandate["mandate_version_id"], {
            "target_id": target_id,
            "status": "active",
            **body.model_dump(),
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return repo.list_assignments(mandate["mandate_version_id"])


@router.delete("/{mandate_id}/investment-targets/{target_id}", status_code=204)
def delete_mandate_target(mandate_id: int, target_id: int, repo: MandateRepository = Depends(_repo)):
    mandate = repo.find_current(mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Capital allocation mandate not found")
    _require_public_demo_mandate(repo, mandate_id)
    if not repo.delete_assignment(mandate["mandate_version_id"], target_id):
        raise HTTPException(status_code=404, detail="Mandate target assignment not found")
