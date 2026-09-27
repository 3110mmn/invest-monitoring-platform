"use client";

import { useEffect, useState } from "react";

/** APIが接続しているDBの分類。接続文字列は受け取らない。 */
type DatabaseEnvironment = "development" | "test" | "demo" | "production";

/** APIへ問い合わせられなかった状態。未表示と区別できるように名前を与える。 */
type BannerState = DatabaseEnvironment | "unreachable";

const STYLES: Record<BannerState, { label: string; className: string }> = {
  // 実データに繋がっているときだけ強く警告する。開発中に見落とすと事故になる。
  production: {
    label: "実データDBに接続しています — ここでの編集は本番に反映されます",
    className: "bg-red-600 text-white",
  },
  demo: {
    label: "デモDB（架空データ）に接続しています",
    className: "bg-slate-700 text-white",
  },
  development: {
    label: "開発DBに接続しています",
    className: "bg-emerald-700 text-white",
  },
  test: {
    label: "テストDBに接続しています",
    className: "bg-amber-600 text-white",
  },
  // 無表示にすると「バナーが未実装」と区別が付かず、接続先不明のまま操作してしまう。
  unreachable: {
    label: "APIに接続できません — 接続先DBを確認できません（バックエンドは起動していますか）",
    className: "bg-gray-800 text-white",
  },
};

/**
 * 接続先DBを画面上部に表示する。
 *
 * ローカルでは開発DBと実データDBで画面の見た目が変わらないため、取り違えると
 * 実データを開発だと思って編集してしまう。接続先はビルド時ではなく実行時に決まるので、
 * `/ready` から取得する。
 */
export default function EnvironmentBanner() {
  const [state, setState] = useState<BannerState | null>(null);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
    // /ready はAPIのルート直下にあり、/api の配下ではない。
    const ready = `${base.replace(/\/api\/?$/, "")}/ready`;
    fetch(ready)
      .then((response) => response.json())
      .then((body) => setState(body.database_environment ?? "unreachable"))
      // 接続先が分からないことを隠さない。無表示は「安全」を意味しない。
      .catch(() => setState("unreachable"));
  }, []);

  // 問い合わせ中だけ何も出さない。結果が出たら必ず何か表示する。
  if (!state) return null;
  const style = STYLES[state];
  if (!style) return null;

  return (
    <div className={`px-6 py-2 text-center text-xs font-medium ${style.className}`}>
      {style.label}
    </div>
  );
}
