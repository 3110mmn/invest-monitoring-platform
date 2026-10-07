"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  getInvestmentTargets,
  getTheme,
  getThemeInvestmentTargets,
  removeThemeInvestmentTarget,
  addThemeInvestmentTarget,
} from "@/lib/api";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import { INVESTMENT_TARGET_TYPE_LABELS } from "@/types";
import type { InvestmentTarget, ThemeConstituent, ThemeDetail } from "@/types";

export default function ThemeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const themeId = Number(use(params).id);
  const [theme, setTheme] = useState<ThemeDetail | null>(null);
  const [constituents, setConstituents] = useState<ThemeConstituent[]>([]);
  const [allTargets, setAllTargets] = useState<InvestmentTarget[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 追加フォームの入力
  const [newTargetId, setNewTargetId] = useState<number | "">("");
  const reloadConstituents = () =>
    getThemeInvestmentTargets(themeId).then(setConstituents);

  useEffect(() => {
    getTheme(themeId).then(setTheme).catch((e) => setError(e.message));
    reloadConstituents().catch((e) => setError(e.message));
    if (!PUBLIC_READ_ONLY) {
      getInvestmentTargets(true).then(setAllTargets).catch((e) => setError(e.message));
    }
    // reloadConstituents は themeId からのみ導かれるため依存に含めない
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [themeId]);

  /** 紐付けの更新系は、成功したら一覧を取り直して表示を実体に合わせる。 */
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await reloadConstituents();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const addConstituent = () =>
    mutate(async () => {
      if (newTargetId === "") return;
      await addThemeInvestmentTarget(themeId, {
        target_id: newTargetId,
      });
      setNewTargetId("");
    });

  if (error) return <p className="text-red-600">{error}</p>;
  if (!theme) return <p className="text-gray-500">読み込み中...</p>;

  const linkedIds = new Set(constituents.map((c) => c.target_id));
  const selectableTargets = allTargets.filter((t) => !linkedIds.has(t.target_id));

  return (
    <div className="space-y-8">
      <div>
        <Link href="/themes" className="text-sm text-gray-500 hover:underline">
          ← テーマ一覧
        </Link>
        <div className="flex items-center gap-3 mt-1">
          <h1 className="text-2xl font-bold">{theme.theme_name}</h1>
          {!theme.is_active && (
            <span className="text-xs px-2 py-0.5 rounded bg-gray-200 text-gray-700">停止中</span>
          )}
        </div>
        <p className="text-sm text-gray-500 font-mono mt-1">{theme.theme_key}</p>
      </div>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">テーマ概要</h2>
        {theme.description ? (
          <p className="text-sm whitespace-pre-wrap leading-relaxed">{theme.description}</p>
        ) : (
          <p className="text-gray-500 text-sm">
            テーマの説明が未記入です。
          </p>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline gap-3">
          <h2 className="text-lg font-semibold">構成銘柄</h2>
          <span className="text-sm text-gray-500">{constituents.length}件</span>
        </div>
        {constituents.length === 0 ? (
          <p className="text-gray-500 text-sm">紐付けられた銘柄がありません。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-100 text-left">
                  <th className="px-3 py-2">銘柄</th>
                  <th className="px-3 py-2">種別</th>
                  <th className="px-3 py-2">所属開始</th>
                  {!PUBLIC_READ_ONLY && <th className="px-3 py-2 text-right">操作</th>}
                </tr>
              </thead>
              <tbody>
                {constituents.map((c) => (
                  <tr key={c.membership_id} className="border-t hover:bg-gray-50">
                    <td className="px-3 py-2">
                      <Link
                        href={`/investment-targets/${c.target_id}`}
                        className="hover:underline"
                      >
                        {c.target_name}
                      </Link>
                      <span className="text-gray-500 font-mono ml-2">{c.target_key}</span>
                    </td>
                    <td className="px-3 py-2">
                      {c.target_type ? INVESTMENT_TARGET_TYPE_LABELS[c.target_type] : "—"}
                    </td>
                    <td className="px-3 py-2 text-gray-600">
                      {new Date(c.effective_from).toLocaleDateString("ja-JP")}
                    </td>
                    {!PUBLIC_READ_ONLY && (
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            mutate(() => removeThemeInvestmentTarget(themeId, c.target_id))
                          }
                          className="text-sm text-red-600 hover:underline disabled:opacity-50"
                        >
                          外す
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-gray-500">
          テーマとの対応はMasterの所属関係です。配分や採用理由などの判断データはここへ保存しません。
          {!PUBLIC_READ_ONLY && "「外す」は現在の所属期間を閉じ、履歴を残します。"}
        </p>

        {!PUBLIC_READ_ONLY && <form
          onSubmit={(e) => {
            e.preventDefault();
            addConstituent();
          }}
          className="border rounded-lg p-4 space-y-3 bg-gray-50"
        >
          <h3 className="font-medium text-sm">銘柄を追加</h3>
          <div className="grid gap-3 items-end">
            <label className="space-y-1">
              <span className="block text-xs text-gray-600">銘柄</span>
              <select
                required
                value={newTargetId}
                onChange={(e) => setNewTargetId(e.target.value ? Number(e.target.value) : "")}
                className="w-full border rounded px-2 py-1 text-sm bg-white"
              >
                <option value="">選択してください</option>
                {selectableTargets.map((target) => (
                  <option key={target.target_id} value={target.target_id}>
                    {target.target_name}（{target.target_key}）
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button
            type="submit"
            disabled={busy || newTargetId === ""}
            className="text-sm bg-gray-900 text-white rounded px-3 py-1.5 disabled:opacity-40"
          >
            追加
          </button>
        </form>}
      </section>
    </div>
  );
}
