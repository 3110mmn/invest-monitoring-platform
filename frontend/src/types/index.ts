// ドメイン型定義 — バックエンド Pydantic モデルと対応

export type InvestmentTargetType =
  | "individual_stock"
  | "etf"
  | "mutual_fund"
  | "bond";

export const INVESTMENT_TARGET_TYPE_LABELS: Record<InvestmentTargetType, string> = {
  individual_stock: "個別株",
  etf: "ETF",
  mutual_fund: "投資信託",
  bond: "債券",
};

export interface Theme {
  theme_id: number;
  theme_key: string;
  theme_name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type ThemeDetail = Theme;

export interface ThemeSummary {
  theme_id: number;
  theme_key: string;
  theme_name: string;
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
  is_monitored: boolean;
  watchlist_status: "considering" | "monitoring" | "paused" | null;
  created_at: string;
  updated_at: string;
}

/** 現在テーマに所属している構成銘柄。 */
export interface ThemeConstituent extends InvestmentTarget {
  membership_id: number;
  effective_from: string;
}

export type MandateStatus = "draft" | "active" | "suspended" | "retired";
export type ReviewCycle = "monthly" | "quarterly" | "semiannual" | "annual" | "ad_hoc" | "other";

export interface MandateTargetAssignment {
  assignment_id: number;
  target_id: number;
  target_key: string;
  target_name: string;
  target_type: InvestmentTargetType | null;
  status: MandateStatus;
  target_weight: number | null;
  target_amount: string | null;
  minimum_weight: number | null;
  maximum_weight: number | null;
  rationale: string | null;
}

export interface CapitalAllocationMandate {
  mandate_id: number;
  mandate_key: string;
  mandate_name: string;
  status: MandateStatus;
  mandate_version_id: number;
  version_no: number;
  purpose: string;
  allocation_weight: number | null;
  budget_amount: string | null;
  currency: string | null;
  expected_return: number | null;
  max_drawdown: number | null;
  horizon_months: number | null;
  benchmark_target_id: number | null;
  benchmark_target_key: string | null;
  benchmark_target_name: string | null;
  review_cycle: ReviewCycle | null;
  review_cycle_custom: string | null;
  next_review_at: string | null;
  effective_from: string;
  effective_until: string | null;
  change_reason: string | null;
  allocated_weight: number;
  unallocated_weight: number | null;
  created_at: string;
  updated_at: string;
  demo_expires_at: string | null;
}

export interface CapitalBudget {
  capital_budget_version_id: number;
  version_no: number;
  total_budget: string;
  currency: string;
  effective_from: string;
  effective_until: string | null;
  change_reason: string;
}

export interface MandateReviewItem {
  mandate_id: number;
  mandate_name: string;
  next_review_at: string;
}

export interface MandateDetail extends CapitalAllocationMandate {
  assignments: MandateTargetAssignment[];
}

export interface MarketPrice {
  target_id: number | null;
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
  target_id: number | null;
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
  target_id: number | null;
  /** 種別を問わない最新の開示。最終更新日の表示に使う */
  latest_disclosure: FinancialDisclosure;
  /** 実績を含む直近の開示。実績値の参照にはこちらを使う */
  latest_actual: FinancialDisclosure | null;
  current_forecast: FinancialDisclosure | null;
  next_forecast: FinancialDisclosure | null;
  dividend_forecast: FinancialDisclosure | null;
}

export interface Security {
  security_key: string;
  jpx_code: string;
  target_key: string | null;
  company_name: string;
  company_name_english: string | null;
  market_code: string | null;
  market_name: string | null;
  sector_17_code: string | null;
  sector_17_name: string | null;
  sector_33_code: string | null;
  sector_33_name: string | null;
  scale_category: string | null;
  target_id: number | null;
  is_watchlisted: boolean;
}

export interface FinancialOverview {
  latest: LatestFinancial | null;
  disclosures: FinancialDisclosure[];
  forecast_history: FinancialDisclosure[];
}
