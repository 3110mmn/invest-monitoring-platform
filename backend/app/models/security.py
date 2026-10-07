"""全市場の銘柄マスタを投資対象画面で参照するAPI契約。"""

from typing import Literal

from pydantic import BaseModel


class SecurityRead(BaseModel):
    security_key: str
    jpx_code: str
    target_key: str | None
    company_name: str
    company_name_english: str | None = None
    market_code: str | None = None
    market_name: str | None = None
    sector_17_code: str | None = None
    sector_17_name: str | None = None
    sector_33_code: str | None = None
    sector_33_name: str | None = None
    scale_category: str | None = None
    target_id: int | None = None
    is_watchlisted: bool = False


class WatchlistUpsert(BaseModel):
    status: Literal["considering", "monitoring", "paused"]
