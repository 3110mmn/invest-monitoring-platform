"""
サービス層で使用するドメインモデル
サービス層の内部で使用するデータクラス
"""
from dataclasses import dataclass


@dataclass
class Theme:
    theme_id: int
    theme_key: str
    theme_name: str
    strategy_id: int
    description: str | None = None
    is_active: bool = True


@dataclass
class Indicator:
    indicator_id: int
    indicator_key: str
    indicator_name: str
    indicator_type: str | None = None
    unit: str | None = None
    freq: str | None = None
    source: str | None = None
    provider_series_id: str | None = None
    is_active: bool = True


@dataclass
class MonitoringLog:
    log_id: int
    indicator_id: int
    obs_date: str
    value_num: float | None
    note: str | None = None


@dataclass
class InvestmentTarget:
    target_id: int
    target_key: str
    target_name: str
    target_type: str | None = None
    market: str | None = None
    currency: str | None = None
    is_monitored: bool = True


@dataclass
class ThemeInvestmentTarget:
    membership_id: int
    theme_id: int
    target_id: int
    effective_from: str
    effective_to: str | None = None
