"use client";

import { useEffect, useState } from "react";
import { use } from "react";
import Link from "next/link";
import PriceChart from "@/components/charts/PriceChart";
import {
  getFinancialOverview,
  getInvestmentTarget,
  getMarketPrices,
} from "@/lib/api";
import { withForecastChanges } from "@/lib/forecast";
import type {
  FinancialDisclosure,
  InvestmentTarget,
  LatestFinancial,
  MarketPrice,
} from "@/types";

const PERIODS = [
  { label: "3ヶ月", days: 90 },
  { label: "6ヶ月", days: 180 },
  { label: "1年", days: 365 },
  { label: "3年", days: 1095 },
];

/** 金額を兆・億・百万で丸めて読みやすくする。NULLは「—」。 */
function formatAmount(value: number | null): string {
  if (value == null) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}兆`;
  if (abs >= 1e8) return `${(value / 1e8).toFixed(0)}億`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(0)}百万`;
  return value.toLocaleString();
}

function formatNumber(value: number | null, suffix = ""): string {
  return value == null ? "—" : `${value.toLocaleString()}${suffix}`;
}

function ForecastCard({
  title,
  disclosure,
  fields,
}: {
  title: string;
  disclosure: FinancialDisclosure | null;
  fields: { label: string; value: (d: FinancialDisclosure) => string }[];
}) {
  return (
    <div className="border rounded p-4">
      <h3 className="font-medium mb-1">{title}</h3>
      {disclosure ? (
        <>
          <p className="text-xs text-gray-500 mb-3">
            {disclosure.disclosed_date} の開示（{disclosure.fiscal_period_type ?? "—"}）より
          </p>
          <dl className="space-y-1 text-sm">
            {fields.map((f) => (
              <div key={f.label} className="flex justify-between gap-4">
                <dt className="text-gray-600">{f.label}</dt>
                <dd className="font-mono">{f.value(disclosure)}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="text-sm text-gray-500 mt-2">この銘柄は開示していません</p>
      )}
    </div>
  );
}

/** 前回開示からの差分。上方修正は緑、下方修正は赤。比較できない場合は表示しない。 */
function Delta({ value, decimals = 0 }: { value: number | null; decimals?: number }) {
  if (value === null || value === 0) return null;
  const magnitude = decimals > 0 ? Math.abs(value).toFixed(decimals) : formatAmount(Math.abs(value));
  return (
    <span className={`ml-2 text-xs ${value > 0 ? "text-green-600" : "text-red-600"}`}>
      {value > 0 ? "+" : "-"}
      {magnitude}
    </span>
  );
}

export default function InvestmentTargetDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const targetId = Number(id);

  const [target, setTarget] = useState<InvestmentTarget | null>(null);
  const [prices, setPrices] = useState<MarketPrice[]>([]);
  const [latest, setLatest] = useState<LatestFinancial | null>(null);
  const [disclosures, setDisclosures] = useState<FinancialDisclosure[]>([]);
  const [forecasts, setForecasts] = useState<FinancialDisclosure[]>([]);
  const [days, setDays] = useState(365);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getInvestmentTarget(targetId).then(setTarget).catch((e) => setError(e.message));
    // 最新値・開示履歴・予想履歴をまとめて取得し、GCS上のParquet走査を1回にする。
    getFinancialOverview(targetId)
      .then((overview) => {
        setLatest(overview.latest);
        setDisclosures(overview.disclosures);
        setForecasts(overview.forecast_history);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [targetId]);

  useEffect(() => {
    getMarketPrices(targetId, days).then(setPrices).catch((e) => setError(e.message));
  }, [targetId, days]);

  if (error) return <p className="text-red-600">{error}</p>;
  if (!target) return <p className="text-gray-500">読み込み中...</p>;

  // 実績は「実績を含む直近の開示」から取る。配当修正だけの開示が最新だと
  // latest_disclosure には実績が入っていないため。
  const actual = latest?.latest_actual ?? null;

  return (
    <div className="space-y-8">
      <div>
        <Link href="/investment-targets" className="text-sm text-gray-500 hover:underline">
          ← 銘柄一覧
        </Link>
        <h1 className="text-2xl font-bold mt-1">
          {target.target_name}{" "}
          <span className="font-mono text-lg text-gray-500">{target.target_key}</span>
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {target.target_type ?? "—"} / {target.market ?? "—"} / {target.currency ?? "—"}
        </p>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">価格</h2>
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <button
                key={p.days}
                onClick={() => setDays(p.days)}
                className={`px-3 py-1 rounded text-sm border ${
                  days === p.days
                    ? "bg-gray-800 text-white border-gray-800"
                    : "border-gray-300 hover:bg-gray-100"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <PriceChart prices={prices} />
        {prices.length > 0 && (
          <p className="text-xs text-gray-500">
            {prices[0].obs_date} 〜 {prices[prices.length - 1].obs_date} / {prices.length}営業日 /
            取得元 {prices[prices.length - 1].source_key} / {prices[prices.length - 1].price_basis}
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">財務</h2>
        {latest && actual ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <ForecastCard
              title="実績"
              disclosure={actual}
              fields={[
                { label: "売上高", value: (d) => formatAmount(d.revenue) },
                { label: "営業利益", value: (d) => formatAmount(d.operating_income) },
                { label: "当期純利益", value: (d) => formatAmount(d.net_income) },
                { label: "EPS", value: (d) => formatNumber(d.eps, " 円") },
                { label: "BPS", value: (d) => formatNumber(d.bps, " 円") },
              ]}
            />
            <ForecastCard
              title="今期業績予想"
              disclosure={latest.current_forecast}
              fields={[
                { label: "売上高", value: (d) => formatAmount(d.forecast_revenue) },
                { label: "営業利益", value: (d) => formatAmount(d.forecast_operating_income) },
                { label: "当期純利益", value: (d) => formatAmount(d.forecast_net_income) },
                { label: "EPS", value: (d) => formatNumber(d.forecast_eps, " 円") },
              ]}
            />
            <ForecastCard
              title="翌期業績予想"
              disclosure={latest.next_forecast}
              fields={[
                { label: "売上高", value: (d) => formatAmount(d.next_forecast_revenue) },
                { label: "営業利益", value: (d) => formatAmount(d.next_forecast_operating_income) },
                { label: "当期純利益", value: (d) => formatAmount(d.next_forecast_net_income) },
                { label: "EPS", value: (d) => formatNumber(d.next_forecast_eps, " 円") },
              ]}
            />
            <ForecastCard
              title="配当"
              disclosure={latest.dividend_forecast ?? actual}
              fields={[
                { label: "年間配当実績", value: (d) => formatNumber(d.annual_dividend_per_share, " 円") },
                {
                  label: "年間配当予想",
                  value: (d) => formatNumber(d.forecast_annual_dividend_per_share, " 円"),
                },
              ]}
            />
          </div>
        ) : (
          <p className="text-gray-500 text-sm">財務開示がありません（ETF・指数などでは提供されません）</p>
        )}
      </section>

      {forecasts.length > 1 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">会社予想の改訂履歴</h2>
          <p className="text-xs text-gray-500">
            決算短信と業績予想の修正の両方を含みます。差分は1つ前の開示との比較で、
            片方の値が無い項目は比較していません。
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-100 text-left">
                  <th className="px-3 py-2">開示日</th>
                  <th className="px-3 py-2">期</th>
                  <th className="px-3 py-2 text-right">売上予想</th>
                  <th className="px-3 py-2 text-right">営業利益予想</th>
                  <th className="px-3 py-2 text-right">純利益予想</th>
                  <th className="px-3 py-2 text-right">EPS予想</th>
                </tr>
              </thead>
              <tbody>
                {withForecastChanges(forecasts).map(({ disclosure, changes }) => (
                  <tr key={disclosure.disclosure_number} className="border-t hover:bg-gray-50">
                    <td className="px-3 py-2 whitespace-nowrap">{disclosure.disclosed_date}</td>
                    <td className="px-3 py-2">{disclosure.fiscal_period_type ?? "—"}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatAmount(disclosure.forecast_revenue)}
                      <Delta value={changes.revenue} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatAmount(disclosure.forecast_operating_income)}
                      <Delta value={changes.operatingIncome} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatAmount(disclosure.forecast_net_income)}
                      <Delta value={changes.netIncome} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatNumber(disclosure.forecast_eps, " 円")}
                      <Delta value={changes.eps} decimals={2} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {disclosures.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">開示履歴</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-100 text-left">
                  <th className="px-3 py-2">開示日</th>
                  <th className="px-3 py-2">期</th>
                  <th className="px-3 py-2">基準</th>
                  <th className="px-3 py-2 text-right">売上高</th>
                  <th className="px-3 py-2 text-right">営業利益</th>
                  <th className="px-3 py-2 text-right">純利益</th>
                  <th className="px-3 py-2 text-right">EPS</th>
                </tr>
              </thead>
              <tbody>
                {disclosures.map((d) => (
                  <tr key={d.disclosure_number} className="border-t hover:bg-gray-50">
                    <td className="px-3 py-2">{d.disclosed_date}</td>
                    <td className="px-3 py-2">{d.fiscal_period_type ?? "—"}</td>
                    <td className="px-3 py-2">{d.accounting_standard ?? "—"}</td>
                    <td className="px-3 py-2 text-right font-mono">{formatAmount(d.revenue)}</td>
                    <td className="px-3 py-2 text-right font-mono">{formatAmount(d.operating_income)}</td>
                    <td className="px-3 py-2 text-right font-mono">{formatAmount(d.net_income)}</td>
                    <td className="px-3 py-2 text-right font-mono">{formatNumber(d.eps)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-gray-500">
            訂正開示は元の開示を上書きせず、別の行として残ります。空欄は未開示または会計基準上存在しない項目です。
          </p>
        </section>
      )}
    </div>
  );
}
