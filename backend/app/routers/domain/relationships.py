"""
テーマ × 銘柄の紐付け管理 API
"""
from fastapi import APIRouter, Depends, HTTPException

from app.database import Connection, get_db
from app.models.relationship import ThemeConstituentRead, ThemeInvestmentTargetCreate
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.theme_repository import ThemeRepository

router = APIRouter(prefix="/relationships", tags=["relationships"])


def _investment_target_repo(conn: Connection = Depends(get_db)) -> InvestmentTargetRepository:
    return InvestmentTargetRepository(conn)


def _require_theme(conn: Connection, theme_id: int) -> None:
    if not ThemeRepository(conn).find_by_id(theme_id):
        raise HTTPException(status_code=404, detail="Theme not found")


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
    _require_theme(conn, theme_id)
    if not repo.find_by_id(body.target_id):
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    repo.add_theme_investment_target(theme_id, body.target_id)
    return repo.find_theme_investment_target(theme_id, body.target_id)


@router.delete("/themes/{theme_id}/investment-targets/{target_id}", status_code=204)
def deactivate_theme_investment_target(
    theme_id: int,
    target_id: int,
    conn: Connection = Depends(get_db),
    repo: InvestmentTargetRepository = Depends(_investment_target_repo),
):
    """テーマから銘柄を外す。行は消さず無効化し、採用していた記録を残す。"""
    _require_theme(conn, theme_id)
    repo.deactivate_theme_investment_target(theme_id, target_id)
