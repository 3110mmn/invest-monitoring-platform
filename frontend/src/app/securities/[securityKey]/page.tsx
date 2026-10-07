"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import PriceChart from "@/components/charts/PriceChart";
import ReturnChart from "@/components/charts/ReturnChart";
import {
  getSecurity,
  getSecurityFinancialOverview,
  getSecurityPrices,
} from "@/lib/api";
import type { FinancialOverview, MarketPrice, Security } from "@/types";

const PERIODS = [
  { label: "3か月", days: 90 },
  { label: "1年", days: 365 },
  { label: "3年", days: 1095 },
];

const amount = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString("ja-JP");

export default function SecurityDetailPage({
  params,
}: {
  params: Promise<{ securityKey: string }>;
}) {
  const { securityKey } = use(params);
  const key = decodeURIComponent(securityKey);
  const [security, setSecurity] = useState<Security | null>(null);
  const [prices, setPrices] = useState<MarketPrice[]>([]);
  const [financials, setFinancials] = useState<FinancialOverview | null>(null);
  const [days, setDays] = useState(365);
  const [chartMode, setChartMode] = useState<"price" | "return">("price");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getSecurity(key), getSecurityFinancialOverview(key)])
      .then(([master, overview]) => {
        setSecurity(master);
        setFinancials(overview);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [key]);

  useEffect(() => {
    getSecurityPrices(key, days)
      .then(setPrices)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [key, days]);

  if (error) return <p className="text-red-600">{error}</p>;
  if (!security) return <p className="text-gray-500">読み込み中...</p>;

  const actual = financials?.latest?.latest_actual ?? null;
  const latestPrice = prices.at(-1);

  return (
    <div className="space-y-8">
      <header>
        <Link href="/investment-targets" className="text-sm text-gray-500 hover:underline">
          ← 投資対象
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{security.company_name}</h1>
          <span className="font-mono text-gray-500">{security.jpx_code.slice(0, 4)}</span>
          {security.is_watchlisted && (
            <span className="rounded bg-blue-100 px-2 py-1 text-xs text-blue-800">監視中</span>
          )}
        </div>
        <p className="mt-1 text-sm text-gray-500">
          {security.market_name ?? "—"} / {security.sector_33_name ?? "—"} / {security.scale_category ?? "—"}
        </p>
        {security.target_id !== null && (
          <Link
            className="mt-2 inline-block text-sm text-blue-700 hover:underline"
            href={`/investment-targets/${security.target_id}`}
          >
            監視対象としての詳細を見る
          </Link>
        )}
      </header>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">価格</h2>
          <div className="flex flex-wrap gap-2">
            <button className="rounded border px-3 py-1 text-sm" onClick={() => setChartMode(chartMode === "price" ? "return" : "price")}>
              {chartMode === "price" ? "累積価格リターンへ" : "価格へ"}
            </button>
            {PERIODS.map((period) => (
              <button
                key={period.days}
                onClick={() => setDays(period.days)}
                className={`rounded border px-3 py-1 text-sm ${days === period.days ? "bg-gray-800 text-white" : "bg-white"}`}
              >
                {period.label}
              </button>
            ))}
          </div>
        </div>
        {chartMode === "price" ? <PriceChart prices={prices} /> : <ReturnChart prices={prices} />}
        {latestPrice && (
          <p className="text-xs text-gray-500">
            最新 {latestPrice.obs_date} / 終値 {amount(latestPrice.close_price)}円 / 株式分割等調整済み・現金配当を含まない
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">最新財務実績</h2>
        {actual ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["売上高", actual.revenue],
              ["営業利益", actual.operating_income],
              ["当期純利益", actual.net_income],
              ["BPS", actual.bps],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded border bg-white p-4">
                <p className="text-xs text-gray-500">{label}</p>
                <p className="mt-1 text-lg font-semibold">{amount(value as number | null)}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">財務開示がありません</p>
        )}
        {actual && (
          <p className="text-xs text-gray-500">
            開示日 {actual.disclosed_date} / 対象期末 {actual.period_end ?? "—"}
          </p>
        )}
      </section>
    </div>
  );
}
