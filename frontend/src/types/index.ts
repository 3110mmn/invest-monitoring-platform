// ドメイン型定義 — バックエンド Pydantic モデルと対応

export type InvestmentTargetType =
  | "individual_stock"
  | "etf"
  | "mutual_fund"
  | "reit"
  | "bond"
  | "index"
  | "commodity";

export interface Theme {
  theme_id: number;
  theme_key: string;
  theme_name: string;
  strategy_id: number;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** テーマ1件。所属strategyのキーと表示名を含む */
export interface ThemeDetail extends Theme {
  strategy_key: string;
  strategy_name: string;
}

export interface ThemeSummary {
  theme_id: number;
  theme_key: string;
  theme_name: string;
  strategy_id: number;
  strategy_key: string;
  strategy_name: string;
  target_count: number;
  is_active: boolean;
}

export interface InvestmentTarget {
  target_id: number;
  target_key: string;
  target_name: string;
  target_type: InvestmentTargetType | null;
  market: string | null;
  currency: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * テーマの構成銘柄。銘柄そのものの属性に、テーマ内での位置づけを添えたもの。
 * `theme_rationale` はそのテーマにこの銘柄を含めた理由で、投資仮説の記録にあたる。
 */
export interface ThemeConstituent extends InvestmentTarget {
  basket_weight: number | null;
  theme_rationale: string | null;
  relation_is_active: boolean;
}

export interface Strategy {
  strategy_id: number;
  strategy_key: string;
  strategy_name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface MarketPrice {
  target_id: number;
  source_key: string;
  obs_date: string;
  open_price: number | null;
  high_price: number | null;
  low_price: number | null;
  close_price: number | null;
  volume: number | null;
  price_basis: string;
  /** DuckDBで算出した日次価格リターン。株式分割等は調整済み、現金配当は含まない */
  daily_return: number | null;
  /** 選択期間の最初の観測を0とした累積価格リターン。現金配当は含まない */
  cumulative_return: number | null;
}

/**
 * 開示1件とその財務値。
 * 値が入る列は開示種別で入れ替わる（FY開示は今期予想を持たず翌期予想を持つ）。
 */
export interface FinancialDisclosure {
  // disclosure_id は持たない。PostgreSQL の surrogate key で、分析層には無い。
  // 開示の同一性は disclosure_number が表す。
  target_id: number;
  source_key: string;
  disclosure_number: string;
  disclosed_date: string;
  disclosed_time: string | null;
  document_type: string;
  fiscal_period_type: string | null;
  period_start: string | null;
  period_end: string | null;
  accounting_standard: string | null;
  reporting_scope: string | null;
  revenue: number | null;
  operating_income: number | null;
  ordinary_income: number | null;
  net_income: number | null;
  eps: number | null;
  total_assets: number | null;
  equity: number | null;
  bps: number | null;
  forecast_revenue: number | null;
  forecast_operating_income: number | null;
  forecast_net_income: number | null;
  forecast_eps: number | null;
  next_forecast_revenue: number | null;
  next_forecast_operating_income: number | null;
  next_forecast_net_income: number | null;
  next_forecast_eps: number | null;
  annual_dividend_per_share: number | null;
  forecast_annual_dividend_per_share: number | null;
}

/** 最新の実績と各予想。出所の開示が異なりうるため開示ごと返る。 */
export interface LatestFinancial {
  target_id: number;
  /** 種別を問わない最新の開示。最終更新日の表示に使う */
  latest_disclosure: FinancialDisclosure;
  /** 実績を含む直近の開示。実績値の参照にはこちらを使う */
  latest_actual: FinancialDisclosure | null;
  current_forecast: FinancialDisclosure | null;
  next_forecast: FinancialDisclosure | null;
  dividend_forecast: FinancialDisclosure | null;
}

export interface FinancialOverview {
  latest: LatestFinancial | null;
  disclosures: FinancialDisclosure[];
  forecast_history: FinancialDisclosure[];
}
