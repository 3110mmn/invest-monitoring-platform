/**
 * バックエンド API クライアント
 * fetch wrappers — 型安全
 */
import type {
  FinancialDisclosure,
  FinancialOverview,
  InvestmentTarget,
  LatestFinancial,
  MarketPrice,
  CapitalAllocationMandate,
  CapitalBudget,
  MandateDetail,
  MandateReviewItem,
  MandateTargetAssignment,
  ReviewCycle,
  Security,
  Theme,
  ThemeConstituent,
  ThemeDetail,
  ThemeSummary,
} from "@/types";
import { request } from "./http";
export { ApiError } from "./http";

// ---- Themes ----
export const getThemes = (isActive?: boolean) =>
  request<Theme[]>(`/themes${isActive != null ? `?is_active=${isActive}` : ""}`);

export const getThemeSummary = (includeArchived = false) =>
  request<ThemeSummary[]>(`/themes/summary${includeArchived ? "?include_archived=true" : ""}`);

export const getTheme = (id: number) => request<ThemeDetail>(`/themes/${id}`);

export const createTheme = (data: Partial<Theme>) =>
  request<Theme>("/themes/", { method: "POST", body: JSON.stringify(data) });

export const updateTheme = (id: number, data: Partial<Theme>) =>
  request<Theme>(`/themes/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteTheme = (id: number) =>
  request<void>(`/themes/${id}`, { method: "DELETE" });

// ---- Investment Targets ----
export const getInvestmentTargets = (isMonitored?: boolean) =>
  request<InvestmentTarget[]>(
    `/investment-targets/${isMonitored != null ? `?is_monitored=${isMonitored}` : ""}`,
  );

export const getInvestmentTarget = (id: number) => request<InvestmentTarget>(`/investment-targets/${id}`);

export const getMarketPrices = (id: number, days = 365) =>
  request<MarketPrice[]>(`/investment-targets/${id}/prices?days=${days}`);

// ---- Financials ----
export const getFinancialDisclosures = (id: number, limit = 50) =>
  request<FinancialDisclosure[]>(`/investment-targets/${id}/financial-disclosures?limit=${limit}`);

/** 会社予想の改訂履歴を新しい順に取得する。予想を持たない開示は含まない。 */
export const getForecastHistory = (id: number, limit = 50) =>
  request<FinancialDisclosure[]>(`/investment-targets/${id}/forecast-history?limit=${limit}`);

export const getLatestFinancial = (id: number) =>
  request<LatestFinancial>(`/investment-targets/${id}/financial-summary/latest`);

export const getFinancialOverview = (id: number, limit = 50) =>
  request<FinancialOverview>(`/investment-targets/${id}/financial-overview?limit=${limit}`);

export const createInvestmentTarget = (data: Partial<InvestmentTarget>) =>
  request<InvestmentTarget>("/investment-targets/", { method: "POST", body: JSON.stringify(data) });

export const updateInvestmentTarget = (id: number, data: Partial<InvestmentTarget>) =>
  request<InvestmentTarget>(`/investment-targets/${id}`, { method: "PATCH", body: JSON.stringify(data) });

// ---- Securities ----
export const getSecurity = (securityKey: string) =>
  request<Security>(`/securities/${encodeURIComponent(securityKey)}`);

export const searchSecurities = (query: string) =>
  request<Security[]>(`/securities/?q=${encodeURIComponent(query)}`);

export const addSecurityToWatchlist = (
  securityKey: string,
  status: "considering" | "monitoring" | "paused",
) => request<Security>(`/securities/${encodeURIComponent(securityKey)}/watchlist`, {
  method: "PUT", body: JSON.stringify({ status }),
});

export const getSecurityPrices = (securityKey: string, days = 365) =>
  request<MarketPrice[]>(
    `/securities/${encodeURIComponent(securityKey)}/prices?days=${days}`,
  );

export const getSecurityFinancialOverview = (securityKey: string, limit = 50) =>
  request<FinancialOverview>(
    `/securities/${encodeURIComponent(securityKey)}/financial-overview?limit=${limit}`,
  );

// ---- Relationships ----
export const getThemeInvestmentTargets = (themeId: number) =>
  request<ThemeConstituent[]>(`/relationships/themes/${themeId}/investment-targets`);

/** テーマへの現在の所属を開始する。 */
export const addThemeInvestmentTarget = (
  themeId: number,
  body: { target_id: number },
) =>
  request<ThemeConstituent>(`/relationships/themes/${themeId}/investment-targets`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/** 全銘柄マスタの銘柄をThemeへ追加する。Watchlistには追加しない。 */
export const addThemeSecurity = (themeId: number, securityKey: string, reason?: string) =>
  request<ThemeConstituent>(
    `/relationships/themes/${themeId}/securities/${encodeURIComponent(securityKey)}`,
    { method: "PUT", body: JSON.stringify({ reason: reason || null }) },
  );

/** テーマから銘柄を外す。現在の所属期間を閉じ、履歴行は残す。 */
export const removeThemeInvestmentTarget = (themeId: number, targetId: number, reason?: string) =>
  request<void>(`/relationships/themes/${themeId}/investment-targets/${targetId}`, {
    method: "DELETE",
    body: JSON.stringify({ reason: reason || null }),
  });

// ---- Data Management ----
export const runDailyUpdate = () =>
  request<{ status: string }>("/data/daily-update", { method: "POST" });

export const runBackfill = () =>
  request<{ status: string }>("/data/backfill", { method: "POST" });

export const initDb = () =>
  request<{ status: string }>("/data/init-db", { method: "POST" });

// ---- Capital Allocation Mandates ----
export const getMandates = () => request<CapitalAllocationMandate[]>("/mandates/");

export const getMandateReviewQueue = () => request<MandateReviewItem[]>("/mandates/review-queue");

export const getCapitalBudget = () => request<CapitalBudget | null>("/mandates/capital-budget");

export const updateCapitalBudget = (data: {
  total_budget: string;
  currency: string;
  change_reason: string;
}) => request<CapitalBudget>("/mandates/capital-budget", {
  method: "PUT",
  body: JSON.stringify(data),
});

export const getMandate = (id: number) => request<MandateDetail>(`/mandates/${id}`);

export const createMandate = (data: {
  mandate_name: string;
  status: string;
  purpose: string;
  allocation_weight?: number | null;
  expected_return?: number | null;
  max_drawdown?: number | null;
  horizon_months?: number | null;
  benchmark_target_id?: number | null;
  benchmark_security_key?: string | null;
  review_cycle?: ReviewCycle | null;
  review_cycle_custom?: string | null;
  next_review_at?: string | null;
  change_reason?: string | null;
}) => request<MandateDetail>("/mandates/", { method: "POST", body: JSON.stringify(data) });

export const updateMandate = (id: number, data: Record<string, unknown>) =>
  request<MandateDetail>(`/mandates/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const deleteMandate = (id: number) =>
  request<void>(`/mandates/${id}`, { method: "DELETE" });

export const upsertMandateTarget = (
  mandateId: number,
  targetId: number,
  data: { target_id: number; status?: string; target_weight?: number | null; minimum_weight?: number | null; maximum_weight?: number | null; rationale?: string | null },
) => request<MandateTargetAssignment[]>(`/mandates/${mandateId}/investment-targets/${targetId}`, {
  method: "PUT",
  body: JSON.stringify(data),
});

export const assignMandateSecurity = (
  mandateId: number,
  securityKey: string,
  data: { target_weight: number; minimum_weight?: number | null; maximum_weight?: number | null; rationale?: string | null },
) => request<MandateTargetAssignment[]>(
  `/mandates/${mandateId}/securities/${encodeURIComponent(securityKey)}`,
  { method: "PUT", body: JSON.stringify(data) },
);

export const deleteMandateTarget = (mandateId: number, targetId: number) =>
  request<void>(`/mandates/${mandateId}/investment-targets/${targetId}`, { method: "DELETE" });
