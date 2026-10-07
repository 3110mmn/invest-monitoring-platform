"""全市場銘柄の検索・詳細・価格・財務API。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics_connection import get_analytics_connection
from app.database import Connection, get_db
from app.etl.normalizers import PRODUCT_TYPES
from app.models.financial import FinancialOverviewRead
from app.models.investment_target import MarketPriceRead
from app.models.security import SecurityRead, WatchlistUpsert
from app.repositories.financial_source import ParquetFinancialSource
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.price_source import ParquetPriceSource
from app.repositories.security_source import ParquetSecuritySource

router = APIRouter(prefix="/securities", tags=["securities"])


def _target_data(security: dict) -> dict:
    return {
        "target_key": security["target_key"],
        "target_name": security["company_name"],
        "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
        "market": security["market_name"],
        "currency": "JPY",
    }


def _analytics():
    connection = get_analytics_connection()
    if connection is None:
        raise HTTPException(503, "Analytics layer is not configured (PARQUET_LAKE)")
    return connection


def _security(security_key: str, conn: Connection) -> dict:
    row = ParquetSecuritySource(_analytics()).find(security_key)
    if row is None:
        raise HTTPException(404, "Security not found")
    watched = (
        InvestmentTargetRepository(conn).find_by_target_key(row["target_key"])
        if row["target_key"]
        else None
    )
    row["target_id"] = watched["target_id"] if watched else None
    row["is_watchlisted"] = bool(watched and watched["watchlist_status"] is not None)
    return row


def _query_target(row: dict) -> dict:
    if not row.get("target_key"):
        raise HTTPException(404, "This security has no market-data target key")
    return {
        "target_id": row.get("target_id"),
        "target_key": row["target_key"],
        "target_name": row["company_name"],
    }


@router.get("/{security_key}", response_model=SecurityRead)
def get_security(security_key: str, conn: Connection = Depends(get_db)):
    return _security(security_key, conn)


@router.get("/", response_model=list[SecurityRead])
def search_securities(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    conn: Connection = Depends(get_db),
):
    query = q.strip()
    if not query:
        raise HTTPException(422, "Search query must not be blank")
    rows = ParquetSecuritySource(_analytics()).search(query, limit)
    targets = InvestmentTargetRepository(conn).find_by_target_keys(
        [row["target_key"] for row in rows if row["target_key"]]
    )
    by_key = {target["target_key"]: target for target in targets}
    for row in rows:
        target = by_key.get(row["target_key"])
        row["target_id"] = target["target_id"] if target else None
        row["is_watchlisted"] = bool(target and target["watchlist_status"] is not None)
    return rows


@router.put("/{security_key}/watchlist", response_model=SecurityRead)
def add_security_to_watchlist(
    security_key: str,
    body: WatchlistUpsert,
    conn: Connection = Depends(get_db),
):
    security = ParquetSecuritySource(_analytics()).find(security_key)
    if security is None or not security["target_key"]:
        raise HTTPException(404, "Security is not watchlist-eligible")
    repo = InvestmentTargetRepository(conn)
    target_id = repo.ensure_from_security(_target_data(security))
    repo.set_watchlist_status(target_id, body.status)
    security["target_id"] = target_id
    security["is_watchlisted"] = True
    return security


@router.get("/{security_key}/prices", response_model=list[MarketPriceRead])
def security_prices(
    security_key: str,
    days: int = Query(default=365, ge=1, le=3650),
    conn: Connection = Depends(get_db),
):
    row = _security(security_key, conn)
    return ParquetPriceSource(_analytics()).price_history(_query_target(row), days=days)


@router.get("/{security_key}/financial-overview", response_model=FinancialOverviewRead)
def security_financial_overview(
    security_key: str,
    limit: int = Query(default=50, ge=1, le=500),
    conn: Connection = Depends(get_db),
):
    row = _security(security_key, conn)
    return ParquetFinancialSource(_analytics()).find_overview(
        _query_target(row), limit=limit
    )
