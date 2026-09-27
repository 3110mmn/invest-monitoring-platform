import { describe, expect, it } from "vitest";
import { withForecastChanges } from "./forecast";
import type { FinancialDisclosure } from "@/types";

function disclosure(
  overrides: Partial<FinancialDisclosure> & { disclosure_number: string },
): FinancialDisclosure {
  return {
    target_id: 1,
    source_key: "jquants",
    disclosed_date: "2026-01-01",
    document_type: "EarnForecastRevision",
    fetched_at: "2026-01-01 00:00:00",
    forecast_revenue: null,
    forecast_operating_income: null,
    forecast_net_income: null,
    forecast_eps: null,
    ...overrides,
  } as FinancialDisclosure;
}

describe("withForecastChanges", () => {
  it("新しい順の入力に対し、時系列で1つ前との差分を付ける", () => {
    const rows = [
      disclosure({ disclosure_number: "new", forecast_revenue: 180, forecast_eps: 65 }),
      disclosure({ disclosure_number: "old", forecast_revenue: 170, forecast_eps: 60 }),
    ];

    const result = withForecastChanges(rows);

    expect(result[0].changes.revenue).toBe(10);
    expect(result[0].changes.eps).toBe(5);
  });

  it("最も古い開示には比較対象が無いので差分は null", () => {
    const rows = [disclosure({ disclosure_number: "only", forecast_revenue: 180 })];

    expect(withForecastChanges(rows)[0].changes.revenue).toBeNull();
  });

  it("下方修正は負の差分になる", () => {
    const rows = [
      disclosure({ disclosure_number: "new", forecast_net_income: 70 }),
      disclosure({ disclosure_number: "old", forecast_net_income: 78 }),
    ];

    expect(withForecastChanges(rows)[0].changes.netIncome).toBe(-8);
  });

  it("片方が欠けている項目は比較しない（ゼロ扱いしない）", () => {
    const rows = [
      disclosure({ disclosure_number: "new", forecast_revenue: 180 }),
      disclosure({ disclosure_number: "old", forecast_revenue: null }),
    ];

    expect(withForecastChanges(rows)[0].changes.revenue).toBeNull();
  });

  it("空の入力では空を返す", () => {
    expect(withForecastChanges([])).toEqual([]);
  });
});
