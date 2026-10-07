"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getThemeSummary } from "@/lib/api";
import type { ThemeSummary } from "@/types";

export default function ThemesPage() {
  const [themes, setThemes] = useState<ThemeSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getThemeSummary().then(setThemes).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">テーマ管理</h1>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {themes.map((t) => (
          <Link
            key={t.theme_id}
            href={`/themes/${t.theme_id}`}
            className="bg-white rounded-lg border p-4 space-y-2 block hover:border-gray-400"
          >
            <div className="flex justify-between items-start">
              <span className="font-semibold">{t.theme_name}</span>
            </div>
            <div className="text-sm text-gray-500 flex gap-4">
              <span>銘柄 {t.target_count}件</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
