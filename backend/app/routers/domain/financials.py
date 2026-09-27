"""財務開示 API — 開示履歴と最新サマリー

**財務は分析層（Parquet）からしか読まない。** `financial_disclosure` /
`financial_summary` を読む経路は置かない。理由と分離の担保は
`app/repositories/financial_source.py` を参照。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics_connection import get_analytics_connection
from app.database import Connection, get_db
from app.models.financial import (
    FinancialDisclosureRead,
    FinancialOverviewRead,
    LatestFinancialRead,
)
from app.repositories.financial_source import ParquetFinancialSource
from app.repositories.investment_target_repository import InvestmentTargetRepository

router = APIRouter(prefix="/investment-targets", tags=["financials"])


def _financials() -> ParquetFinancialSource:
    """財務の読み出し元。分析層へ繋がっていなければ503。

    値を返せないのに200を返すと、開示が無いのか設定が漏れたのか区別できない。
    """
    analytics = get_analytics_connection()
    if analytics is None:
        raise HTTPException(
            status_code=503,
            detail="Analytics layer is not configured (PARQUET_LAKE)",
        )
    return ParquetFinancialSource(analytics)


def _require_target(conn: Connection, target_id: int) -> dict:
    """銘柄の実在はControl Plane（PostgreSQL）が答える。分析層に管理状態は無い。"""
    target = InvestmentTargetRepository(conn).find_by_id(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="InvestmentTarget not found")
    return target


@router.get("/{target_id}/financial-overview", response_model=FinancialOverviewRead)
def financial_overview(
    target_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    conn: Connection = Depends(get_db),
    financials: ParquetFinancialSource = Depends(_financials),
):
    """詳細画面用の最新値・開示履歴・予想履歴を1走査で返す。"""
    return financials.find_overview(_require_target(conn, target_id), limit=limit)


@router.get("/{target_id}/financial-disclosures", response_model=list[FinancialDisclosureRead])
def financial_disclosures(
    target_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    conn: Connection = Depends(get_db),
    financials: ParquetFinancialSource = Depends(_financials),
):
    """開示履歴を新しい順に返す。訂正開示は元の開示と別レコードとして含む。"""
    return financials.find_disclosures(_require_target(conn, target_id), limit=limit)


@router.get(
    "/{target_id}/forecast-history",
    response_model=list[FinancialDisclosureRead],
)
def forecast_history(
    target_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    conn: Connection = Depends(get_db),
    financials: ParquetFinancialSource = Depends(_financials),
):
    """会社予想の改訂履歴を新しい順に返す。

    決算短信と業績予想の修正の両方を含む。予想を持たない開示は除く。
    """
    return financials.find_forecast_history(_require_target(conn, target_id), limit=limit)


@router.get("/{target_id}/financial-summary/latest", response_model=LatestFinancialRead)
def latest_financial_summary(
    target_id: int,
    conn: Connection = Depends(get_db),
    financials: ParquetFinancialSource = Depends(_financials),
):
    """最新の実績と、今期・翌期の予想を返す。

    最新の開示が今期予想を持たない場合があるため、予想はそれぞれ値を持つ最新の開示から
    取得し、出所の開示ごと返す。
    """
    latest = financials.find_latest(_require_target(conn, target_id))
    if latest is None:
        raise HTTPException(status_code=404, detail="No financial disclosure found")
    return latest
