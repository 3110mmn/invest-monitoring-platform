"use client";

import Link from "next/link";
import { DemoUnavailable } from "@/components/DemoUnavailable";
import { FormEvent, use, useEffect, useState } from "react";
import { BenchmarkSecurityPicker } from "@/components/BenchmarkSecurityPicker";
import {
  deleteMandateTarget,
  assignMandateSecurity,
  getInvestmentTargets,
  getMandate,
  searchSecurities,
  updateMandate,
  upsertMandateTarget,
} from "@/lib/api";
import { REVIEW_CYCLE_LABELS, suggestedReviewDate } from "@/lib/review-cycle";
import { PUBLIC_DEMO_WRITES, PUBLIC_READ_ONLY } from "@/lib/runtime";
import type { InvestmentTarget, MandateDetail, ReviewCycle, Security } from "@/types";

const money = (value: string | null, currency: string | null) =>
  value == null || currency == null ? "未設定" :
  new Intl.NumberFormat("ja-JP", { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(value));

export default function MandateDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const mandateId = Number(id);
  const [mandate, setMandate] = useState<MandateDetail | null>(null);
  const [targets, setTargets] = useState<InvestmentTarget[]>([]);
  const [securityQuery, setSecurityQuery] = useState("");
  const [securityResults, setSecurityResults] = useState<Security[]>([]);
  const [selectedSecurity, setSelectedSecurity] = useState<Security | null>(null);
  const [existingTargetId, setExistingTargetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reviewCycle, setReviewCycle] = useState<ReviewCycle | "">("");
  const [nextReviewAt, setNextReviewAt] = useState("");
  const canEditMandate = mandate !== null && (
    !PUBLIC_READ_ONLY || (PUBLIC_DEMO_WRITES && mandate.demo_expires_at !== null)
  );

  const load = async () => {
    try {
      const [detail, allTargets] = await Promise.all([getMandate(mandateId), getInvestmentTargets()]);
      setMandate(detail);
      setReviewCycle(detail.review_cycle ?? "");
      setNextReviewAt(detail.next_review_at ?? "");
      setTargets(allTargets);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => {
    let cancelled = false;
    Promise.all([getMandate(mandateId), getInvestmentTargets()])
      .then(([detail, allTargets]) => {
        if (cancelled) return;
        setMandate(detail);
        setReviewCycle(detail.review_cycle ?? "");
        setNextReviewAt(detail.next_review_at ?? "");
        setTargets(allTargets);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [mandateId]);

  useEffect(() => {
    const query = securityQuery.trim();
    if (query.length < 2) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      searchSecurities(query)
        .then((rows) => { if (!cancelled) setSecurityResults(rows.filter((row) => row.target_key)); })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [securityQuery]);

  const addTarget = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!selectedSecurity && !existingTargetId) {
      setError("全銘柄マスタまたは登録済みの投資対象から選択してください");
      return;
    }
    try {
      const allocation = {
        target_weight: Number(form.get("target_weight")) / 100,
        minimum_weight: form.get("minimum_weight") ? Number(form.get("minimum_weight")) / 100 : null,
        maximum_weight: form.get("maximum_weight") ? Number(form.get("maximum_weight")) / 100 : null,
        rationale: String(form.get("rationale") || "") || null,
      };
      if (selectedSecurity) {
        await assignMandateSecurity(mandateId, selectedSecurity.security_key, allocation);
      } else {
        await upsertMandateTarget(mandateId, Number(existingTargetId), {
          target_id: Number(existingTargetId), ...allocation,
        });
      }
      event.currentTarget.reset();
      setSelectedSecurity(null);
      setExistingTargetId("");
      setSecurityQuery("");
      setSecurityResults([]);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const revise = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!mandate) return;
    const form = new FormData(event.currentTarget);
    try {
      await updateMandate(mandateId, {
        mandate_name: String(form.get("mandate_name")),
        status: String(form.get("status")),
        purpose: String(form.get("purpose")),
        allocation_weight: form.get("allocation_weight") ? Number(form.get("allocation_weight")) / 100 : null,
        expected_return: form.get("expected_return") ? Number(form.get("expected_return")) / 100 : null,
        max_drawdown: form.get("max_drawdown") ? Number(form.get("max_drawdown")) / 100 : null,
        horizon_months: form.get("horizon_months") ? Number(form.get("horizon_months")) : null,
        benchmark_target_id: form.get("benchmark_target_id") ? Number(form.get("benchmark_target_id")) : null,
        benchmark_security_key: String(form.get("benchmark_security_key") || "") || null,
        review_cycle: String(form.get("review_cycle") || "") || null,
        review_cycle_custom: reviewCycle === "other"
          ? String(form.get("review_cycle_custom") || "") || null
          : null,
        next_review_at: nextReviewAt || null,
        change_reason: String(form.get("change_reason")),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error && !mandate) return <p className="text-red-600">{error}</p>;
  if (!mandate) return <p className="text-gray-500">読み込み中...</p>;

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">{mandate.mandate_name}</h1>
          <span className="rounded bg-gray-100 px-2 py-1 text-xs">v{mandate.version_no}</span>
        </div>
        <p className="mt-2 text-gray-600">{mandate.purpose}</p>
        {PUBLIC_DEMO_WRITES && <p className="mt-2 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">公開デモは共有環境です。公開デモで追加された期限付き投資枠は他の閲覧者も編集できます。作成から1時間後に画面から非表示となり、定期処理で物理削除されます。総投資予算と銘柄マスタは変更できません。</p>}
        {PUBLIC_DEMO_WRITES && mandate.demo_expires_at && <p className="mt-2 text-xs text-amber-700">この投資枠の削除予定: {new Date(mandate.demo_expires_at).toLocaleString("ja-JP")}</p>}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <section className="rounded-lg border bg-white p-4"><p className="text-xs text-gray-500">予算枠</p><p className="mt-1 text-xl font-semibold">{money(mandate.budget_amount, mandate.currency)}</p></section>
        <section className="rounded-lg border bg-white p-4"><p className="text-xs text-gray-500">総投資予算に対する割合</p><p className="mt-1 text-xl font-semibold">{mandate.allocation_weight == null ? "-" : `${(mandate.allocation_weight * 100).toFixed(1)}%`}</p></section>
        <section className="rounded-lg border bg-white p-4"><p className="text-xs text-gray-500">期待リターン</p><p className="mt-1 text-xl font-semibold">{mandate.expected_return == null ? "-" : `${(mandate.expected_return * 100).toFixed(1)}%`}</p></section>
        <section className="rounded-lg border bg-white p-4"><p className="text-xs text-gray-500">許容DD</p><p className="mt-1 text-xl font-semibold">{mandate.max_drawdown == null ? "-" : `${(mandate.max_drawdown * 100).toFixed(1)}%`}</p></section>
        <section className="rounded-lg border bg-white p-4"><p className="text-xs text-gray-500">未配分</p><p className="mt-1 text-xl font-semibold">{mandate.unallocated_weight == null ? "-" : `${(mandate.unallocated_weight * 100).toFixed(1)}%`}</p></section>
      </div>
      <p className="text-sm text-gray-600">
        比較ベンチマーク：
        {mandate.benchmark_target_id == null ? (
          "未設定"
        ) : (
          <Link
            href={`/investment-targets/${mandate.benchmark_target_id}`}
            className="text-blue-600 hover:underline"
          >
            {mandate.benchmark_target_name}
          </Link>
        )}
      </p>

      <section className="rounded-lg border bg-white p-5">
        <h2 className="font-semibold">投資対象への目標配分</h2>
        <table className="mt-4 w-full text-sm">
          <thead><tr className="border-b text-left"><th className="py-2">投資対象</th><th>配分</th><th>目標金額</th><th>理由</th><th /></tr></thead>
          <tbody>{mandate.assignments.map((allocation) => (
            <tr key={allocation.target_id} className="border-b">
              <td className="py-3">
                <Link
                  href={`/investment-targets/${allocation.target_id}`}
                  className="text-blue-600 hover:underline"
                >
                  {allocation.target_name}
                </Link>
                <span className="ml-2 font-mono text-xs text-gray-400">{allocation.target_key}</span>
              </td>
              <td>{allocation.target_weight == null ? "-" : `${(allocation.target_weight * 100).toFixed(1)}%`}</td>
              <td>{money(allocation.target_amount, mandate.currency)}</td>
              <td className="text-gray-600">{allocation.rationale ?? "-"}</td>
              <td>{canEditMandate && <button onClick={async () => { try { await deleteMandateTarget(mandateId, allocation.target_id); await load(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }} className="text-xs text-red-600">解除</button>}</td>
            </tr>
          ))}</tbody>
        </table>
        {canEditMandate && (
          <form onSubmit={addTarget} className="mt-4 grid gap-3 md:grid-cols-6">
            <div className="md:col-span-2">
              <input value={securityQuery} onChange={(e) => { setSecurityQuery(e.target.value); setSelectedSecurity(null); setSecurityResults([]); }} placeholder="全銘柄からコード・銘柄名で検索" className="w-full rounded border p-2 text-sm" />
              {PUBLIC_READ_ONLY && <DemoUnavailable>検索は試せますが、全銘柄からの追加はできません。下の「登録済み対象から選択」は使えます。</DemoUnavailable>}
              {selectedSecurity ? <p className="text-xs text-blue-700">選択中: {selectedSecurity.company_name} ({selectedSecurity.jpx_code})</p> : null}
              {securityResults.length > 0 && !selectedSecurity ? (
                <ul className="max-h-48 overflow-y-auto rounded border bg-white text-sm">
                  {securityResults.map((security) => <li key={security.security_key}>
                    <button type="button" disabled={PUBLIC_READ_ONLY} onClick={() => { setSelectedSecurity(security); setExistingTargetId(""); setSecurityResults([]); }} className="w-full px-2 py-1 text-left hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-400 disabled:hover:bg-transparent">{security.company_name} ({security.jpx_code})</button>
                  </li>)}
                </ul>
              ) : null}
            </div>
            <select name="existing_target_id" value={existingTargetId} onChange={(e) => { setExistingTargetId(e.target.value); setSelectedSecurity(null); setSecurityResults([]); }} className="rounded border p-2 text-sm">
              <option value="">または登録済み対象から選択</option>
              {targets.map((target) => <option key={target.target_id} value={target.target_id}>{target.target_name} ({target.target_key})</option>)}
            </select>
            <input name="target_weight" required type="number" min="0" max="100" step="0.1" placeholder="目標配分 %" className="rounded border p-2 text-sm" />
            <input name="minimum_weight" type="number" min="0" max="100" step="0.1" placeholder="下限 %" className="rounded border p-2 text-sm" />
            <input name="maximum_weight" type="number" min="0" max="100" step="0.1" placeholder="上限 %" className="rounded border p-2 text-sm" />
            <input name="rationale" placeholder="採用理由" className="rounded border p-2 text-sm" />
            <button className="rounded bg-blue-700 px-4 py-2 text-sm text-white">追加・更新</button>
          </form>
        )}
      </section>

        {canEditMandate && (
        <form onSubmit={revise} className="grid gap-4 rounded-lg border bg-white p-5 md:grid-cols-2 lg:grid-cols-4">
          <div className="md:col-span-2 lg:col-span-4">
            <h2 className="font-semibold">投資枠を改訂</h2>
            <p className="mt-1 text-sm text-gray-600">変更内容は過去versionを上書きせず、新しいversionとして保存します。</p>
          </div>
          <label className="text-sm"><span className="mb-1 block font-medium">投資枠名</span><input name="mandate_name" required defaultValue={mandate.mandate_name} className="w-full rounded border p-2" /></label>
          {PUBLIC_READ_ONLY ? <input type="hidden" name="status" value="draft" /> : <label className="text-sm"><span className="mb-1 block font-medium">運用状態</span><select name="status" defaultValue={mandate.status} className="w-full rounded border p-2"><option value="draft">下書き</option><option value="active">運用中</option><option value="suspended">停止中</option><option value="retired">終了</option></select></label>}
          <label className="text-sm"><span className="mb-1 block font-medium">投資予算に対する割合</span><input name="allocation_weight" type="number" min="0" max="100" step="0.1" defaultValue={mandate.allocation_weight == null ? "" : mandate.allocation_weight * 100} className="w-full rounded border p-2" /></label>
            <div className="text-sm"><span className="mb-1 block font-medium">比較ベンチマーク</span><BenchmarkSecurityPicker key={`${mandate.mandate_version_id}-${mandate.benchmark_target_id ?? "none"}`} targets={targets} currentTargetId={mandate.benchmark_target_id} catalogSelectable={!PUBLIC_READ_ONLY} /></div>
          <label className="text-sm"><span className="mb-1 block font-medium">期待リターン（年率）</span><input name="expected_return" type="number" min="-100" max="1000" step="0.1" defaultValue={mandate.expected_return == null ? "" : mandate.expected_return * 100} className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">許容最大ドローダウン</span><input name="max_drawdown" type="number" min="-100" max="0" step="0.1" defaultValue={mandate.max_drawdown == null ? "" : mandate.max_drawdown * 100} className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">想定投資期間（月）</span><input name="horizon_months" type="number" min="1" defaultValue={mandate.horizon_months ?? ""} className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">見直し周期</span><select name="review_cycle" value={reviewCycle} onChange={(event) => { const cycle = event.target.value as ReviewCycle | ""; setReviewCycle(cycle); setNextReviewAt(suggestedReviewDate(cycle)); }} className="w-full rounded border p-2"><option value="">定期見直しなし</option>{Object.entries(REVIEW_CYCLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {reviewCycle === "other" && <label className="text-sm"><span className="mb-1 block font-medium">その他の見直し周期</span><input name="review_cycle_custom" required defaultValue={mandate.review_cycle_custom ?? ""} className="w-full rounded border p-2" /></label>}
          <label className="text-sm"><span className="mb-1 block font-medium">次回見直し日</span><input name="next_review_at" type="date" value={nextReviewAt} onChange={(event) => setNextReviewAt(event.target.value)} className="w-full rounded border p-2" /><span className="mt-1 block text-xs text-gray-500">周期変更時に自動入力し、手動でも修正できます。</span></label>
          <label className="text-sm md:col-span-2 lg:col-span-4"><span className="mb-1 block font-medium">投資目的・運用方針</span><textarea name="purpose" required defaultValue={mandate.purpose} className="w-full rounded border p-2" /></label>
          <label className="text-sm md:col-span-2 lg:col-span-3"><span className="mb-1 block font-medium">改訂理由</span><input name="change_reason" required placeholder="例：年間レビューで配分比率を変更" className="w-full rounded border p-2" /></label>
          <button className="self-end rounded bg-gray-800 px-4 py-2 text-sm text-white">新しいversionとして保存</button>
        </form>
      )}
    </div>
  );
}
