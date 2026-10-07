"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { addSecurityToWatchlist, getInvestmentTargets, searchSecurities, updateInvestmentTarget } from "@/lib/api";
import { DemoUnavailable } from "@/components/DemoUnavailable";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import { INVESTMENT_TARGET_TYPE_LABELS } from "@/types";
import type { InvestmentTarget, InvestmentTargetType, Security } from "@/types";

export default function InvestmentTargetsPage() {
  const [investmentTargets, setInvestmentTargets] = useState<InvestmentTarget[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [monitoringFilter, setMonitoringFilter] = useState<"ALL" | "MONITORED" | "CONSIDERING" | "PAUSED" | "UNLISTED">("MONITORED");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Security[]>([]);

  const reload = () => getInvestmentTargets().then(setInvestmentTargets);

  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      searchSecurities(term)
        .then((rows) => { if (!cancelled) setResults(rows.filter((row) => row.target_key)); })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query]);

  useEffect(() => {
    reload().catch((e) => setError(e.message));
  }, []);

  const types: Array<{ value: "ALL" | InvestmentTargetType; label: string }> = [
    { value: "ALL", label: "すべて" },
    { value: "individual_stock", label: INVESTMENT_TARGET_TYPE_LABELS.individual_stock },
    { value: "etf", label: INVESTMENT_TARGET_TYPE_LABELS.etf },
    { value: "mutual_fund", label: INVESTMENT_TARGET_TYPE_LABELS.mutual_fund },
    { value: "bond", label: INVESTMENT_TARGET_TYPE_LABELS.bond },
  ];
  const displayed = investmentTargets.filter((target) => {
    const matchesType = filter === "ALL" || target.target_type === filter;
    const matchesMonitoring =
      monitoringFilter === "ALL" ||
      (monitoringFilter === "MONITORED" && target.watchlist_status === "monitoring") ||
      (monitoringFilter === "CONSIDERING" && target.watchlist_status === "considering") ||
      (monitoringFilter === "PAUSED" && target.watchlist_status === "paused") ||
      (monitoringFilter === "UNLISTED" && target.watchlist_status === null);
    return matchesType && matchesMonitoring;
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">ウォッチリスト</h1>
        <p className="mt-1 text-sm text-gray-600">継続的に監視する銘柄・ETF等を管理します。</p>
      </div>
      {error && <p className="text-red-600">{error}</p>}

      <section className="rounded-lg border bg-white p-4">
        <label htmlFor="security-search" className="text-sm font-medium">全銘柄マスタからWatchlistへ追加</label>
        <input id="security-search" value={query} onChange={(e) => { setQuery(e.target.value); setResults([]); }} placeholder="コード・銘柄名で検索" className="mt-2 w-full rounded border p-2 text-sm" />
        {PUBLIC_READ_ONLY && <DemoUnavailable>検索は試せますが、Watchlistへの追加はできません。</DemoUnavailable>}
        {results.length > 0 && <ul className="mt-2 max-h-48 overflow-y-auto text-sm">
          {results.map((row) => <li key={row.security_key} className="flex items-center justify-between border-t py-2">
            <span>{row.company_name} ({row.jpx_code})</span>
            <button disabled={PUBLIC_READ_ONLY} className="text-blue-700 disabled:cursor-not-allowed disabled:text-gray-400" onClick={async () => {
              try { await addSecurityToWatchlist(row.security_key, "considering"); await reload(); setQuery(""); setResults([]); setError(null); }
              catch (e) { setError(e instanceof Error ? e.message : String(e)); }
            }}>検討中に追加</button>
          </li>)}
        </ul>}
      </section>

      <div className="space-y-3 rounded-lg border bg-white p-4">
        <div className="flex flex-wrap gap-2">
          {(["ALL", "CONSIDERING", "MONITORED", "PAUSED", "UNLISTED"] as const).map((status) => (
            <button
              key={status}
              onClick={() => setMonitoringFilter(status)}
              className={`rounded border px-3 py-1 text-sm font-medium ${
                monitoringFilter === status
                  ? "border-blue-700 bg-blue-700 text-white"
                  : "border-gray-300 hover:bg-gray-100"
              }`}
            >
              {{ ALL: "すべて", CONSIDERING: "検討中", MONITORED: "監視中", PAUSED: "監視停止", UNLISTED: "未登録",  }[status]}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {types.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`rounded border px-3 py-1 text-sm font-medium ${
                filter === value ? "border-gray-800 bg-gray-800 text-white" : "border-gray-300 hover:bg-gray-100"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-gray-100 text-left">
            <th className="px-3 py-2">ティッカー</th>
            <th className="px-3 py-2">銘柄名</th>
            <th className="px-3 py-2">タイプ</th>
            <th className="px-3 py-2">市場</th>
            <th className="px-3 py-2">通貨</th>
            <th className="px-3 py-2">状態</th>
          </tr>
        </thead>
        <tbody>
          {displayed.map((target) => (
            <tr key={target.target_id} className="border-t hover:bg-gray-50">
              <td className="px-3 py-2 font-mono">
                <Link href={`/investment-targets/${target.target_id}`} className="text-blue-700 hover:underline">
                  {target.target_key}
                </Link>
              </td>
              <td className="px-3 py-2">{target.target_name}</td>
              <td className="px-3 py-2">{target.target_type ? INVESTMENT_TARGET_TYPE_LABELS[target.target_type] : "-"}</td>
              <td className="px-3 py-2">{target.market ?? "-"}</td>
              <td className="px-3 py-2">{target.currency ?? "-"}</td>
              <td className="px-3 py-2">
                <span className={target.is_monitored ? "badge-ok" : "text-gray-400"}>
                  {target.watchlist_status === "monitoring" ? "監視中" : target.watchlist_status === "considering" ? "検討中" : target.watchlist_status === "paused" ? "監視停止" : "未登録"}
                </span>
                <select disabled={PUBLIC_READ_ONLY} value={target.watchlist_status ?? "none"} onChange={async (e) => {
                  try {
                    await updateInvestmentTarget(target.target_id, { watchlist_status: e.target.value === "none" ? null : e.target.value as InvestmentTarget["watchlist_status"] });
                    await reload(); setError(null);
                  } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
                }} className="ml-2 rounded border text-xs disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400">
                  <option value="none">未登録</option><option value="considering">検討中</option><option value="monitoring">監視中</option><option value="paused">監視停止</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
