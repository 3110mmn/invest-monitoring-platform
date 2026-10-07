"""
銘柄 (InvestmentTarget) CRUD + 価格履歴 API
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import UniqueViolation

from app.analytics_connection import get_analytics_connection
from app.database import Connection, get_db
from app.models.investment_target import (
    InvestmentTargetCreate,
    InvestmentTargetRead,
    InvestmentTargetUpdate,
    LatestMarketPriceRead,
    MarketPriceRead,
)
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.price_source import ParquetPriceSource

router = APIRouter(prefix="/investment-targets", tags=["investment-targets"])


def _repo(conn: Connection = Depends(get_db)) -> InvestmentTargetRepository:
    return InvestmentTargetRepository(conn)


def _prices() -> ParquetPriceSource:
    """価格の読み出し元。経路は1本で、常に分析層を読む。

    公開デモも同じ経路を通す。実データとデモの分離はコードの分岐ではなくバケットの
    権限で担保する。分析層へ繋がっていなければ、設定の誤りとして503を返す。
    値を返せないのに200を返すと、価格が無いのか設定が漏れたのか区別できない。
    """
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(
            status_code=503,
            detail="Analytics layer is not configured (PARQUET_LAKE)",
        )
    return ParquetPriceSource(analytics)


@router.get("/", response_model=list[InvestmentTargetRead])
def list_investment_targets(
    is_monitored: bool | None = None,
    repo: InvestmentTargetRepository = Depends(_repo),
):
    return repo.find_all(is_monitored=is_monitored)


@router.get("/latest-prices", response_model=list[LatestMarketPriceRead])
def latest_prices(
    repo: InvestmentTargetRepository = Depends(_repo),
    prices: ParquetPriceSource = Depends(_prices),
):
    return prices.latest_prices(repo.find_all(is_monitored=True))


@router.get("/{target_id}", response_model=InvestmentTargetRead)
def get_investment_target(target_id: int, repo: InvestmentTargetRepository = Depends(_repo)):
    investment_target = repo.find_by_id(target_id)
    if not investment_target:
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    return investment_target


@router.get("/{target_id}/prices", response_model=list[MarketPriceRead])
def investment_target_prices(
    target_id: int,
    days: int = Query(default=30, ge=1, le=3650),
    repo: InvestmentTargetRepository = Depends(_repo),
    prices: ParquetPriceSource = Depends(_prices),
):
    # 銘柄の実在はControl Plane（PostgreSQL）が答える。分析層には管理状態が無い。
    target = repo.find_by_id(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    return prices.price_history(target, days=days)


@router.post("/", response_model=InvestmentTargetRead, status_code=201)
def create_investment_target(body: InvestmentTargetCreate, repo: InvestmentTargetRepository = Depends(_repo)):
    try:
        new_id = repo.create(body.model_dump())
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="target_key already exists") from exc
    return repo.find_by_id(new_id)


@router.patch("/{target_id}", response_model=InvestmentTargetRead)
def update_investment_target(
    target_id: int, body: InvestmentTargetUpdate, repo: InvestmentTargetRepository = Depends(_repo)
):
    if not repo.find_by_id(target_id):
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    repo.update(target_id, body.model_dump(exclude_unset=True))
    return repo.find_by_id(target_id)


@router.delete("/{target_id}", status_code=204)
def delete_investment_target(target_id: int, repo: InvestmentTargetRepository = Depends(_repo)):
    if not repo.find_by_id(target_id):
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    repo.set_watchlist_status(target_id, None)
