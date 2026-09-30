"""Pydantic スキーマ — investment_target / market_price_observation"""

from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import InvestmentTargetType


class InvestmentTargetBase(BaseModel):
    target_key: str
    target_name: str
    target_type: InvestmentTargetType | None = None
    market: str | None = None
    currency: str | None = None


class InvestmentTargetCreate(InvestmentTargetBase):
    pass


class InvestmentTargetUpdate(BaseModel):
    target_name: str | None = None
    target_type: InvestmentTargetType | None = None
    market: str | None = None
    currency: str | None = None
    is_active: bool | None = None


class InvestmentTargetRead(InvestmentTargetBase):
    target_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MarketPriceRead(BaseModel):
    # log_id / fetched_at / note / created_at は持たない。分析層のParquetには無く、
    # フロントも参照していない。読み出し元によって形が変わらないようにする。
    target_id: int
    source_key: str
    ingestion_run_id: int | None = None
    obs_date: date
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None
    volume: float | None = None
    price_basis: str
    # Derived。保存値ではなく、preferred_priceからリクエスト時に計算する。
    # フィールド名は互換性のため維持するが、意味は現金配当を含まないPrice Return。
    daily_return: float | None = None
    cumulative_return: float | None = None


class LatestMarketPriceRead(BaseModel):
    target_id: int
    target_key: str
    target_name: str
    target_type: InvestmentTargetType | None = None
    latest_date: date
    close_price: float | None = None
    source_key: str
    ingestion_run_id: int | None = None
    price_basis: str
