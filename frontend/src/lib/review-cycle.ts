import type { ReviewCycle } from "@/types";

export const REVIEW_CYCLE_LABELS: Record<ReviewCycle, string> = {
  monthly: "毎月",
  quarterly: "四半期",
  semiannual: "半年",
  annual: "年次",
  ad_hoc: "都度",
  other: "その他",
};

export const todayInJapan = () =>
  new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });

export function suggestedReviewDate(cycle: ReviewCycle | "", baseDate = todayInJapan()) {
  const months: Partial<Record<ReviewCycle, number>> = {
    monthly: 1,
    quarterly: 3,
    semiannual: 6,
    annual: 12,
  };
  const increment = cycle ? months[cycle] : undefined;
  if (increment == null) return "";

  const [year, month, day] = baseDate.split("-").map(Number);
  const targetMonthIndex = month - 1 + increment;
  const targetYear = year + Math.floor(targetMonthIndex / 12);
  const targetMonth = targetMonthIndex % 12;
  const lastDay = new Date(targetYear, targetMonth + 1, 0).getDate();
  const result = new Date(targetYear, targetMonth, Math.min(day, lastDay));
  return result.toLocaleDateString("sv-SE");
}
