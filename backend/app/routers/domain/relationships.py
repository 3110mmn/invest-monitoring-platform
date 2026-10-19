"""
テーマ × 銘柄の紐付け管理 API
"""
from fastapi import APIRouter, Depends, HTTPException

from app.analytics_connection import get_analytics_connection
from app.database import Connection, get_db
from app.etl.normalizers import PRODUCT_TYPES
from app.models.relationship import (
    ThemeConstituentRead,
    ThemeInvestmentTargetCreate,
    ThemeMembershipChange,
)
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.security_source import ParquetSecuritySource
from app.repositories.theme_repository import ThemeRepository

router = APIRouter(prefix="/relationships", tags=["relationships"])


def _investment_target_repo(conn: Connection = Depends(get_db)) -> InvestmentTargetRepository:
    return InvestmentTargetRepository(conn)


def _require_theme(conn: Connection, theme_id: int) -> dict:
    theme = ThemeRepository(conn).find_by_id(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    return theme


def _require_active_theme(conn: Connection, theme_id: int) -> None:
    if not _require_theme(conn, theme_id)["is_active"]:
        raise HTTPException(
            status_code=409,
            detail="アーカイブ済みThemeへ銘柄は追加できません。先に有効へ戻してください",
        )


# ---- theme_investment_target ----

@router.get(
    "/themes/{theme_id}/investment-targets",
    response_model=list[ThemeConstituentRead],
)
def list_theme_investment_targets(
    theme_id: int,
    repo: InvestmentTargetRepository = Depends(_investment_target_repo),
):
    """テーマに現在所属している構成銘柄を返す。"""
    return repo.get_theme_investment_targets(theme_id)


@router.post(
    "/themes/{theme_id}/investment-targets",
    response_model=ThemeConstituentRead,
    status_code=201,
)
def add_theme_investment_target(
    theme_id: int,
    body: ThemeInvestmentTargetCreate,
    conn: Connection = Depends(get_db),
    repo: InvestmentTargetRepository = Depends(_investment_target_repo),
):
    """テーマへの所属期間を開始する。既に所属中なら冪等に現在行を返す。"""
    _require_active_theme(conn, theme_id)
    if not repo.find_by_id(body.target_id):
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    repo.add_theme_investment_target(theme_id, body.target_id, reason=body.reason)
    return repo.find_theme_investment_target(theme_id, body.target_id)


@router.put(
    "/themes/{theme_id}/securities/{security_key}",
    response_model=ThemeConstituentRead,
)
def add_theme_security_from_catalog(
    theme_id: int,
    security_key: str,
    body: ThemeMembershipChange | None = None,
    conn: Connection = Depends(get_db),
):
    """全銘柄マスタからThemeへ追加する。Watchlistには追加しない。"""
    _require_active_theme(conn, theme_id)
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(503, "Analytics layer is not configured (PARQUET_LAKE)")
    security = ParquetSecuritySource(analytics).find(security_key)
    if security is None or not security["target_key"]:
        raise HTTPException(404, "Security is not assignable")
    repo = InvestmentTargetRepository(conn)
    target_id = repo.ensure_from_security({
        "target_key": security["target_key"],
        "target_name": security["company_name"],
        "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
        "market": security["market_name"],
        "currency": "JPY",
    })
    repo.add_theme_investment_target(
        theme_id,
        target_id,
        reason=body.reason if body else None,
    )
    return repo.find_theme_investment_target(theme_id, target_id)


@router.delete("/themes/{theme_id}/investment-targets/{target_id}", status_code=204)
def deactivate_theme_investment_target(
    theme_id: int,
    target_id: int,
    body: ThemeMembershipChange | None = None,
    conn: Connection = Depends(get_db),
    repo: InvestmentTargetRepository = Depends(_investment_target_repo),
):
    """テーマから銘柄を外す。行は消さず無効化し、採用していた記録を残す。"""
    _require_active_theme(conn, theme_id)
    repo.deactivate_theme_investment_target(
        theme_id,
        target_id,
        reason=body.reason if body else None,
    )
