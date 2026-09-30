"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getInvestmentTargets } from "@/lib/api";
import type { InvestmentTarget } from "@/types";

export default function InvestmentTargetsPage() {
  const [investmentTargets, setInvestmentTargets] = useState<InvestmentTarget[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getInvestmentTargets().then(setInvestmentTargets).catch((e) => setError(e.message));
  }, []);

  const types = ["ALL", "individual_stock", "etf", "mutual_fund", "reit", "bond", "index", "commodity"];
  const displayed = filter === "ALL" ? investmentTargets : investmentTargets.filter((target) => target.target_type === filter);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">銘柄管理</h1>
      {error && <p className="text-red-600">{error}</p>}

      <div className="flex gap-2">
        {types.map((t) => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`px-3 py-1 rounded text-sm font-medium border ${
              filter === t ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 hover:bg-gray-100"
            }`}
          >
            {t}
          </button>
        ))}
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
              <td className="px-3 py-2">{target.target_type ?? "-"}</td>
              <td className="px-3 py-2">{target.market ?? "-"}</td>
              <td className="px-3 py-2">{target.currency ?? "-"}</td>
              <td className="px-3 py-2">
                <span className={target.is_monitored ? "badge-ok" : "text-gray-400"}>
                  {target.is_monitored ? "監視中" : "監視停止"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
