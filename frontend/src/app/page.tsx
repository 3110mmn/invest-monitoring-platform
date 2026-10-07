"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getMandateReviewQueue } from "@/lib/api";
import { todayInJapan } from "@/lib/review-cycle";
import { PUBLIC_READ_ONLY } from "@/lib/runtime";
import type { MandateReviewItem } from "@/types";

export default function Dashboard() {
  const [reviewQueue, setReviewQueue] = useState<MandateReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const today = todayInJapan();

  useEffect(() => {
    let cancelled = false;
    getMandateReviewQueue()
      .then((items) => {
        if (!cancelled) setReviewQueue(items);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">ダッシュボード</h1>
      <p className="text-gray-600">対応が必要なトリガーと、見直し予定を確認します。</p>

      <section className="rounded-lg border bg-white p-5">
        <h2 className="font-semibold">トリガー発火</h2>
        <p className="mt-2 text-sm text-gray-600">発火履歴の取得・表示は未実装です。Trigger Instance の実装後にここへ表示します。</p>
      </section>

      <section className="rounded-lg border border-amber-200 bg-amber-50 p-5">
        <h2 className="font-semibold">見直し予定（期日順）</h2>
        <p className="mt-1 text-xs text-gray-600">投資枠に設定された次回見直し日を、投資枠単位で表示します。</p>
        {loading ? <p className="mt-3 text-sm text-gray-600">読み込み中...</p> : error ? (
          <p role="alert" className="mt-3 text-sm text-red-700">見直し予定を取得できませんでした：{error}</p>
        ) : reviewQueue.length === 0 ? (
          <p className="mt-3 text-sm text-gray-600">見直し日が設定された有効な投資枠はありません。</p>
        ) : (
          <div className="mt-3 divide-y divide-amber-200">
            {reviewQueue.map((item) => {
              const overdue = item.next_review_at <= today;
              return (
                <div key={item.mandate_id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                  <div>
                    <Link href={`/mandates/${item.mandate_id}`} className="font-medium hover:underline">{item.mandate_name}投資枠の見直し</Link>
                  </div>
                  <span className={overdue ? "font-semibold text-red-700" : "text-gray-700"}>次回見直し日：{item.next_review_at}{overdue ? "（期限到来）" : ""}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>
      {/* 読み取り専用モードでは設定画面への導線が無いため、公開デモの前提はここで説明する。 */}
      {PUBLIC_READ_ONLY && (
        <section className="rounded-lg border border-blue-100 bg-blue-50 p-5">
          <h2 className="font-semibold text-blue-900">このデモについて</h2>
          <p className="mt-2 text-sm text-blue-900">
            表示しているテーマ・銘柄・株価・財務は<strong>すべて架空のデモ用データ</strong>です。
            市場データの利用条件が第三者への提供を禁じているため、公開環境には実データを置かず、
            架空データだけを持つ別のデータベースを参照しています。
          </p>
          <p className="mt-2 text-sm text-blue-900">
            実データを扱う環境は非公開で、公開側とデータ・認証情報・データベースを共有しません。
            両者は同じスキーマとAPIを使うため、画面の挙動は実環境と同じです。
          </p>
          <p className="mt-2 text-sm text-blue-900">
            この環境は閲覧のみ可能です。更新系のAPIは実装済みですが、
            公開環境ではHTTPの入口とデータベースの権限の両方で書き込みを拒否しています。
          </p>
        </section>
      )}
    </div>
  );
}
