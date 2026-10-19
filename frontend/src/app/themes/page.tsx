"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { createTheme, getThemes, getThemeSummary } from "@/lib/api";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import type { Theme, ThemeSummary } from "@/types";

export default function ThemesPage() {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [counts, setCounts] = useState<Map<number, number>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  const load = async () => {
    const [rows, summaries] = await Promise.all([
      getThemes(showArchived ? undefined : true),
      getThemeSummary(showArchived),
    ]);
    setThemes(rows);
    setCounts(new Map(summaries.map((row: ThemeSummary) => [row.theme_id, row.target_count])));
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getThemes(showArchived ? undefined : true),
      getThemeSummary(showArchived),
    ])
      .then(([rows, summaries]) => {
        if (cancelled) return;
        setThemes(rows);
        setCounts(new Map(summaries.map((row) => [row.theme_id, row.target_count])));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [showArchived]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    try {
      await createTheme({
        theme_name: String(data.get("theme_name") || "").trim(),
        description: String(data.get("description") || "").trim() || null,
      });
      form.reset();
      await load();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">テーマ管理</h1>
      <p className="text-sm text-gray-600">
        Themeは分析・監視対象をまとめるユーザー定義のグループです。取引所の業種分類そのものではありません。
      </p>
      {error && <p className="text-red-600">{error}</p>}

      {!PUBLIC_READ_ONLY && (
        <form onSubmit={create} className="rounded-lg border bg-gray-50 p-4 space-y-3">
          <h2 className="font-semibold">テーマを作成</h2>
          <div className="grid gap-3">
            <label className="space-y-1 text-sm">
              <span className="block text-gray-600">名称</span>
              <input name="theme_name" required placeholder="EDINET DB 小売Pilot" className="w-full rounded border px-3 py-2" />
            </label>
          </div>
          <label className="space-y-1 text-sm block">
            <span className="block text-gray-600">定義・グルーピング基準</span>
            <textarea
              name="description"
              rows={4}
              placeholder={"対象：東証上場J-REIT\n除外：インフラファンド\n生成方法：手動\n基準時点：2026-10-19"}
              className="w-full rounded border px-3 py-2"
            />
          </label>
          <button disabled={busy} className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50">作成</button>
        </form>
      )}

      <label className="flex items-center gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
        アーカイブ済みを表示
      </label>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {themes.map((theme) => (
          <Link key={theme.theme_id} href={`/themes/${theme.theme_id}`} className="bg-white rounded-lg border p-4 space-y-2 block hover:border-gray-400">
            <div className="flex justify-between items-start gap-2">
              <span className="font-semibold">{theme.theme_name}</span>
              {!theme.is_active && <span className="rounded bg-gray-200 px-2 py-0.5 text-xs">アーカイブ済み</span>}
            </div>
            <p className="text-xs font-mono text-gray-500">{theme.theme_key}</p>
            <p className="line-clamp-3 whitespace-pre-wrap text-sm text-gray-600">
              {theme.description || "定義・グルーピング基準が未記入です。"}
            </p>
            <div className="text-sm text-gray-500">銘柄 {counts.get(theme.theme_id) ?? 0}件</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
