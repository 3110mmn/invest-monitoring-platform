import type { Metadata } from "next";
import Link from "next/link";
import EnvironmentBanner from "@/components/EnvironmentBanner";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import "./globals.css";

export const metadata: Metadata = {
  title: "投資判断プラットフォーム",
  description: "テーマ管理・指標モニタリング・トリガー判定・ポートフォリオ管理",
};

const NAV = [
  { href: "/", label: "ダッシュボード" },
  { href: "/themes", label: "テーマ" },
  { href: "/investment-targets", label: "投資対象" },
  ...(!PUBLIC_READ_ONLY ? [{ href: "/settings", label: "設定" }] : []),
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body className="min-h-screen bg-gray-50 text-gray-900">
        <nav className="bg-white border-b border-gray-200 px-6 py-3 flex gap-6">
          {NAV.map(({ href, label }) => (
            <Link key={href} href={href} className="text-sm font-medium hover:text-blue-600">
              {label}
            </Link>
          ))}
        </nav>
        {/* 公開環境では接続先が自明なので出さない。取り違えが起きるのはローカルだけ。 */}
        {!PUBLIC_READ_ONLY && <EnvironmentBanner />}
        {PUBLIC_READ_ONLY && (
          <div className="border-b border-blue-100 bg-blue-50 px-6 py-2 text-center text-xs text-blue-800">
            公開デモ — 表示データはすべて架空です。閲覧のみ可能です
          </div>
        )}
        <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
