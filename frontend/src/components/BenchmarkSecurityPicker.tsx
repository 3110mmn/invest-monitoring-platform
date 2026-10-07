"use client";

import { useEffect, useState } from "react";
import { DemoUnavailable } from "@/components/DemoUnavailable";
import { searchSecurities } from "@/lib/api";
import type { InvestmentTarget, Security } from "@/types";

export function BenchmarkSecurityPicker({
  targets,
  currentTargetId,
  catalogSelectable = true,
}: {
  targets: InvestmentTarget[];
  currentTargetId?: number | null;
  /**
   * 全銘柄マスタから選べるか。公開デモではAPIが422で拒否するため false になる。
   * **検索欄は隠さない。** 検索自体は動くので、できることまで少なく見せない。
   */
  catalogSelectable?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Security[]>([]);
  const [selectedSecurity, setSelectedSecurity] = useState<Security | null>(null);
  const [targetId, setTargetId] = useState(currentTargetId == null ? "" : String(currentTargetId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const normalized = query.trim();
    if (normalized.length < 2) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      searchSecurities(normalized)
        .then((rows) => {
          if (!cancelled) {
            setResults(rows.filter((row) => row.target_key));
            setError(null);
          }
        })
        .catch((cause) => {
          if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const chooseSecurity = (security: Security) => {
    setSelectedSecurity(security);
    setTargetId("");
    setResults([]);
    setQuery("");
  };

  return (
    <div className="space-y-2">
      <>
        {catalogSelectable && <input type="hidden" name="benchmark_security_key" value={selectedSecurity?.security_key ?? ""} />}
        <label className="block text-sm">
          <span className="mb-1 block font-medium">全銘柄マスタから検索</span>
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelectedSecurity(null);
              setTargetId("");
            }}
            placeholder="コード・銘柄名で検索"
            className="w-full rounded border p-2"
          />
        </label>
        {!catalogSelectable && (
          <DemoUnavailable>
            検索は試せますが、全銘柄からの指定はできません。下の「登録済み投資対象」は使えます。
          </DemoUnavailable>
        )}
      </>
      {results.length > 0 && (
        <ul className="max-h-40 overflow-y-auto rounded border bg-white text-sm">
          {results.map((security) => (
            <li key={security.security_key}>
              <button type="button" disabled={!catalogSelectable} onClick={() => chooseSecurity(security)} className="w-full px-2 py-1 text-left hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-400 disabled:hover:bg-transparent">
                {security.company_name} ({security.jpx_code})
              </button>
            </li>
          ))}
        </ul>
      )}
      {selectedSecurity && <p className="text-xs text-blue-700">選択中：{selectedSecurity.company_name} ({selectedSecurity.jpx_code})</p>}
      {error && <p role="alert" className="text-xs text-red-600">銘柄検索に失敗しました：{error}</p>}
      <label className="block text-sm">
        <span className="mb-1 block font-medium">または登録済み投資対象</span>
        <select
          name="benchmark_target_id"
          value={targetId}
          onChange={(event) => {
            setTargetId(event.target.value);
            setSelectedSecurity(null);
          }}
          className="w-full rounded border p-2"
        >
          <option value="">未設定</option>
          {targets.map((target) => <option key={target.target_id} value={target.target_id}>{target.target_name} ({target.target_key})</option>)}
        </select>
      </label>
    </div>
  );
}
