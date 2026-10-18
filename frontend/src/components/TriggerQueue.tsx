"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { COMPARISON_LABELS, getTriggerEvaluations, METRIC_LABELS, type TriggerEvaluation } from "@/lib/triggers";
import { todayInJapan } from "@/lib/review-cycle";

export default function TriggerQueue() {
  const [rows, setRows] = useState<TriggerEvaluation[]>([]);
  const [error, setError] = useState("");
  const today = todayInJapan();
  useEffect(() => {
    let cancelled = false;
    getTriggerEvaluations()
      .then((items) => { if (!cancelled) setRows(items.filter((item) => item.mode === "live" && item.status === "fired")); })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { cancelled = true; };
  }, []);
  const pending = rows.filter((row) => row.response_count === 0);
  const due = rows.filter((row) => row.next_review_on != null && row.next_review_on <= today);
  const visible = [...pending, ...due.filter((row) => !pending.some((item) => item.evaluation_id === row.evaluation_id))];
  return <div className="mt-2 space-y-2">
    {error ? <p role="alert" className="text-sm text-red-700">発火履歴を取得できませんでした：{error}</p>
      : visible.length ? visible.slice(0, 5).map((row) => <p key={row.evaluation_id} className="text-sm">
        <Link href={`/triggers#evaluation-${row.evaluation_id}`} className="text-blue-700 underline">
          {row.target_name}：{row.conditions.length === 1
            ? `${METRIC_LABELS[row.metric_key]}の${COMPARISON_LABELS[row.comparison_type]}`
            : `${row.conditions.length}条件（${row.logic_operator === "all" ? "すべて" : "いずれか"}）`}
        </Link>
        {row.response_count === 0 ? "（未対応）" : "（次回確認日到来）"}
      </p>) : <p className="text-sm text-gray-600">対応が必要な実運用トリガーはありません。</p>}
  </div>;
}
