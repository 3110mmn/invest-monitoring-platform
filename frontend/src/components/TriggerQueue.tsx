"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { COMPARISON_LABELS, getTriggerEvaluations, METRIC_LABELS, type TriggerEvaluation } from "@/lib/triggers";

export default function TriggerQueue() {
  const [rows, setRows] = useState<TriggerEvaluation[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    getTriggerEvaluations()
      .then((items) => { if (!cancelled) setRows(items.filter((item) => item.mode === "live" && item.status === "fired")); })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { cancelled = true; };
  }, []);
  return <div className="mt-2 space-y-2">
    {error ? <p role="alert" className="text-sm text-red-700">発火履歴を取得できませんでした：{error}</p>
      : rows.length ? rows.slice(0, 5).map((row) => <p key={row.evaluation_id} className="text-sm">
        <Link href={`/triggers#evaluation-${row.evaluation_id}`} className="text-blue-700 underline">
          {row.target_name}：{METRIC_LABELS[row.metric_key]}の{COMPARISON_LABELS[row.comparison_type]}
        </Link>
      </p>) : <p className="text-sm text-gray-600">対応が必要な実運用トリガーはありません。</p>}
  </div>;
}
