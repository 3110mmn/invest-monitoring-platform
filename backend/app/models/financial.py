"""Pydantic スキーマ — financial_disclosure / financial_summary"""

from datetime import date, time

from pydantic import BaseModel


class FinancialSummaryValues(BaseModel):
    """開示に対応する財務値。

    値が入る列は開示種別によって入れ替わる。FY開示は今期予想を持たず翌期予想を持つ。
    未提供・会計基準上存在しない項目は NULL とし、ゼロで補完しない。
    """

    reporting_scope: str | None = None
    revenue: int | None = None
    operating_income: int | None = None
    ordinary_income: int | None = None
    net_income: int | None = None
    eps: float | None = None
    total_assets: int | None = None
    equity: int | None = None
    bps: float | None = None
    operating_cash_flow: int | None = None
    investing_cash_flow: int | None = None
    financing_cash_flow: int | None = None
    cash_equivalents: int | None = None
    forecast_revenue: int | None = None
    forecast_operating_income: int | None = None
    forecast_ordinary_income: int | None = None
    forecast_net_income: int | None = None
    forecast_eps: float | None = None
    next_forecast_revenue: int | None = None
    next_forecast_operating_income: int | None = None
    next_forecast_ordinary_income: int | None = None
    next_forecast_net_income: int | None = None
    next_forecast_eps: float | None = None
    annual_dividend_per_share: float | None = None
    forecast_annual_dividend_per_share: float | None = None
    shares_outstanding: int | None = None
    treasury_shares: int | None = None
    average_shares: int | None = None


class FinancialDisclosureRead(FinancialSummaryValues):
    """1開示とその財務値。"""

    # `disclosure_id` は持たない。PostgreSQLのsurrogate keyで、分析層には無い。
    # 開示の同一性は `disclosure_number` が表す。
    target_id: int | None
    source_key: str
    disclosure_number: str
    disclosed_date: date
    disclosed_time: time | None = None
    document_type: str
    fiscal_period_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    fiscal_year_start: date | None = None
    fiscal_year_end: date | None = None
    accounting_standard: str | None = None
    ingestion_run_id: int | None = None

    model_config = {"from_attributes": True}


class LatestFinancialRead(BaseModel):
    """最新の実績・今期業績予想・翌期業績予想・配当予想。

    それぞれ出所の開示が異なりうるため、開示ごと返して値の根拠を辿れるようにする。
    業績予想と配当予想は会社が別々に開示するため分けている。業績予想を出さず配当予想
    だけ出す企業では、`current_forecast` が null で `dividend_forecast` に値が入る。

    `latest_disclosure` は種別を問わない最新の開示で、最終更新日の表示に使う。
    配当修正だけの開示が最新の場合、そこに実績は入っていない。
    **実績値の参照には `latest_actual` を使うこと。**
    """

    target_id: int | None
    latest_disclosure: FinancialDisclosureRead
    latest_actual: FinancialDisclosureRead | None = None
    current_forecast: FinancialDisclosureRead | None = None
    next_forecast: FinancialDisclosureRead | None = None
    dividend_forecast: FinancialDisclosureRead | None = None


class FinancialOverviewRead(BaseModel):
    """銘柄詳細画面で使う財務データ一式。Parquetは1回だけ走査する。"""

    latest: LatestFinancialRead | None = None
    disclosures: list[FinancialDisclosureRead]
    forecast_history: list[FinancialDisclosureRead]
