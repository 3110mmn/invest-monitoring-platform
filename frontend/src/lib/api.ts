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
  Strategy,
  Theme,
  ThemeConstituent,
  ThemeDetail,
  ThemeSummary,
} from "@/types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

/** APIのエラー応答。成功時は素のボディで、エラー時だけこの形に揃う。 */
interface ApiErrorBody {
  error: { status: number; code: string; message: string; details?: unknown[] };
}

/**
 * APIがエラー応答を返したことを表す。
 * 呼び出し側が「データが無い(404)」と「障害(5xx等)」を区別できるように status を持つ。
 * `code` はステータスから決まる機械可読な値で、message の文言に依存せず分岐できる。
 * 通信断やJSONパース失敗は素の Error のままなので、これも障害として扱われる。
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** エラーボディから表示用のメッセージとコードを取り出す。形式が違っても落とさない。 */
async function readError(res: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    if (body?.error?.message) {
      return { message: body.error.message, code: body.error.code };
    }
  } catch {
    // JSONでない応答（プロキシのHTMLエラーページ等）はステータス表記へ落とす
  }
  return { message: res.statusText || `HTTP ${res.status}` };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const { message, code } = await readError(res);
    throw new ApiError(res.status, message, code);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

// ---- Themes ----
export const getThemes = (isActive?: boolean) =>
  request<Theme[]>(`/themes${isActive != null ? `?is_active=${isActive}` : ""}`);

export const getThemeSummary = () => request<ThemeSummary[]>("/themes/summary");

export const getTheme = (id: number) => request<ThemeDetail>(`/themes/${id}`);

export const createTheme = (data: Partial<Theme>) =>
  request<Theme>("/themes/", { method: "POST", body: JSON.stringify(data) });

export const updateTheme = (id: number, data: Partial<Theme>) =>
  request<Theme>(`/themes/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteTheme = (id: number) =>
  request<void>(`/themes/${id}`, { method: "DELETE" });

// ---- Investment Targets ----
export const getInvestmentTargets = (isActive?: boolean) =>
  request<InvestmentTarget[]>(`/investment-targets/${isActive != null ? `?is_active=${isActive}` : ""}`);

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

// ---- Relationships ----
export const getThemeInvestmentTargets = (themeId: number) =>
  request<ThemeConstituent[]>(`/relationships/themes/${themeId}/investment-targets`);

/** テーマに銘柄を追加、または既存の紐付けを更新する。外した銘柄を渡すと復帰する。 */
export const upsertThemeInvestmentTarget = (
  themeId: number,
  body: { target_id: number; basket_weight?: number; rationale?: string | null },
) =>
  request<ThemeConstituent>(`/relationships/themes/${themeId}/investment-targets`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/** テーマから銘柄を外す。行は消えず、無効な紐付けとして記録が残る。 */
export const removeThemeInvestmentTarget = (themeId: number, targetId: number) =>
  request<void>(`/relationships/themes/${themeId}/investment-targets/${targetId}`, {
    method: "DELETE",
  });

// ---- Data Management ----
export const runDailyUpdate = () =>
  request<{ status: string }>("/data/daily-update", { method: "POST" });

export const runBackfill = () =>
  request<{ status: string }>("/data/backfill", { method: "POST" });

export const initDb = () =>
  request<{ status: string }>("/data/init-db", { method: "POST" });

// ---- Strategies ----
export const getStrategies = () => request<Strategy[]>("/themes/strategies/");

export const createStrategy = (data: Partial<Strategy>) =>
  request<Strategy>("/themes/strategies/", { method: "POST", body: JSON.stringify(data) });
