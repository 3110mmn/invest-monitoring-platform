"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, use, useEffect, useState } from "react";
import {
  addThemeSecurity,
  deleteTheme,
  getTheme,
  getThemeInvestmentTargets,
  removeThemeInvestmentTarget,
  searchSecurities,
  updateTheme,
} from "@/lib/api";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import { INVESTMENT_TARGET_TYPE_LABELS } from "@/types";
import type { Security, ThemeConstituent, ThemeDetail } from "@/types";

export default function ThemeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const themeId = Number(use(params).id);
  const router = useRouter();
  const [theme, setTheme] = useState<ThemeDetail | null>(null);
  const [constituents, setConstituents] = useState<ThemeConstituent[]>([]);
  const [securityQuery, setSecurityQuery] = useState("");
  const [securityResults, setSecurityResults] = useState<Security[]>([]);
  const [selectedSecurity, setSelectedSecurity] = useState<Security | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reloadConstituents = () =>
    getThemeInvestmentTargets(themeId).then(setConstituents);

  useEffect(() => {
    getTheme(themeId).then(setTheme).catch((e) => setError(e.message));
    reloadConstituents().catch((e) => setError(e.message));
    // reloadConstituents は themeId からのみ導かれるため依存に含めない
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [themeId]);

  useEffect(() => {
    const query = securityQuery.trim();
    if (query.length < 2 || selectedSecurity) {
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchSecurities(query)
        .then((rows) => { if (!cancelled) setSecurityResults(rows.filter((row) => row.target_key)); })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [securityQuery, selectedSecurity]);

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
      if (!selectedSecurity) return;
      const reason = window.prompt("追加理由（任意）\n例：REIT一覧のCSVに掲載されているため");
      if (reason === null) return;
      await addThemeSecurity(themeId, selectedSecurity.security_key, reason.trim());
      setSelectedSecurity(null);
      setSecurityQuery("");
      setSecurityResults([]);
    });

  const saveTheme = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const updated = await updateTheme(themeId, {
        theme_name: String(data.get("theme_name") || "").trim(),
        description: String(data.get("description") || "").trim() || null,
        is_active: data.get("is_active") === "true",
      });
      setTheme(updated);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const permanentlyDeleteTheme = async () => {
    if (!window.confirm("このThemeを完全削除しますか。この操作は元に戻せません。")) return;
    setBusy(true);
    try {
      await deleteTheme(themeId);
      router.push("/themes");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <p className="text-red-600">{error}</p>;
  if (!theme) return <p className="text-gray-500">読み込み中...</p>;

  return (
    <div className="space-y-8">
      <div>
        <Link href="/themes" className="text-sm text-gray-500 hover:underline">
          ← テーマ一覧
        </Link>
        <div className="flex items-center gap-3 mt-1">
          <h1 className="text-2xl font-bold">{theme.theme_name}</h1>
          {!theme.is_active && (
            <span className="text-xs px-2 py-0.5 rounded bg-gray-200 text-gray-700">アーカイブ済み</span>
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
            定義・グルーピング基準が未記入です。
          </p>
        )}
        {!PUBLIC_READ_ONLY && (
          <form onSubmit={saveTheme} className="rounded-lg border bg-gray-50 p-4 space-y-3">
            <label className="block space-y-1 text-sm">
              <span className="text-gray-600">名称</span>
              <input name="theme_name" required defaultValue={theme.theme_name} className="w-full rounded border px-3 py-2" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-gray-600">定義・グルーピング基準</span>
              <textarea name="description" rows={5} defaultValue={theme.description ?? ""} className="w-full rounded border px-3 py-2" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-gray-600">状態</span>
              <select name="is_active" defaultValue={String(theme.is_active)} className="w-full rounded border bg-white px-3 py-2">
                <option value="true">有効</option>
                <option value="false">アーカイブ</option>
              </select>
            </label>
            <div className="flex gap-3">
              <button disabled={busy} className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50">保存</button>
              <button type="button" disabled={busy} onClick={permanentlyDeleteTheme} className="rounded border border-red-300 px-4 py-2 text-sm text-red-700 disabled:opacity-50">
                Themeを完全削除
              </button>
            </div>
          </form>
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
                  <th className="px-3 py-2">追加理由</th>
                  {!PUBLIC_READ_ONLY && theme.is_active && <th className="px-3 py-2 text-right">操作</th>}
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
                    <td className="px-3 py-2 text-gray-600">{c.added_reason || "—"}</td>
                    {!PUBLIC_READ_ONLY && theme.is_active && (
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            const reason = window.prompt("除外理由（任意）\n例：分類誤りのため除外");
                            if (reason === null) return;
                            mutate(() => removeThemeInvestmentTarget(themeId, c.target_id, reason.trim()));
                          }}
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
          テーマとの対応はMasterの所属関係です。投資判断上の採用理由ではなく、分類を変更した運用上の理由を保存します。
          {!PUBLIC_READ_ONLY && theme.is_active && "「外す」は現在の所属期間を閉じ、履歴を残します。"}
        </p>

        {!PUBLIC_READ_ONLY && theme.is_active && <form
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
              <input
                value={securityQuery}
                onChange={(e) => { setSecurityQuery(e.target.value); setSelectedSecurity(null); setSecurityResults([]); }}
                placeholder="銘柄コードまたは名称を2文字以上入力"
                className="w-full border rounded px-3 py-2 text-sm bg-white"
              />
            </label>
            {securityResults.length > 0 && (
              <div className="max-h-48 overflow-y-auto rounded border bg-white">
                {securityResults.map((security) => (
                  <button
                    type="button"
                    key={security.security_key}
                    onClick={() => {
                      setSelectedSecurity(security);
                      setSecurityQuery(`${security.company_name}（${security.target_key}）`);
                    }}
                    className="block w-full border-b px-3 py-2 text-left text-sm last:border-b-0 hover:bg-gray-50"
                  >
                    {security.company_name} <span className="font-mono text-gray-500">{security.target_key}</span>
                  </button>
                ))}
              </div>
            )}
            {selectedSecurity && (
              <p className="text-xs text-blue-700">選択中: {selectedSecurity.company_name}（{selectedSecurity.target_key}）</p>
            )}
          </div>
          <button
            type="submit"
            disabled={busy || !selectedSecurity}
            className="text-sm bg-gray-900 text-white rounded px-3 py-1.5 disabled:opacity-40"
          >
            追加
          </button>
        </form>}
        {!PUBLIC_READ_ONLY && !theme.is_active && (
          <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            アーカイブ済みThemeの構成は変更できません。変更する場合はThemeを有効へ戻してください。
          </p>
        )}
      </section>
    </div>
  );
}
