"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { BenchmarkSecurityPicker } from "@/components/BenchmarkSecurityPicker";
import {
  createMandate,
  deleteMandate,
  getCapitalBudget,
  getInvestmentTargets,
  getMandates,
  updateCapitalBudget,
} from "@/lib/api";
import { REVIEW_CYCLE_LABELS, suggestedReviewDate, todayInJapan } from "@/lib/review-cycle";
import { PUBLIC_DEMO_WRITES, PUBLIC_READ_ONLY } from "@/lib/runtime";
import type { CapitalAllocationMandate, CapitalBudget, InvestmentTarget, ReviewCycle } from "@/types";

const money = (value: string | null, currency: string | null) =>
  value == null || currency == null ? "未設定" : new Intl.NumberFormat("ja-JP", {
    style: "currency", currency, maximumFractionDigits: 0,
  }).format(Number(value));

export default function MandatesPage() {
  const [mandates, setMandates] = useState<CapitalAllocationMandate[]>([]);
  const [targets, setTargets] = useState<InvestmentTarget[]>([]);
  const [capitalBudget, setCapitalBudget] = useState<CapitalBudget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewCycle, setReviewCycle] = useState<ReviewCycle | "">("");
  const [nextReviewAt, setNextReviewAt] = useState("");
  const today = todayInJapan();
  const canCreateDemoMandates = !PUBLIC_READ_ONLY || PUBLIC_DEMO_WRITES;

  const load = async () => {
    try {
      const [mandateRows, targetRows, budget] = await Promise.all([
        getMandates(), getInvestmentTargets(), getCapitalBudget(),
      ]);
      setMandates(mandateRows);
      setTargets(targetRows);
      setCapitalBudget(budget);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([getMandates(), getInvestmentTargets(), getCapitalBudget()])
      .then(([mandateRows, targetRows, budget]) => {
        if (cancelled) return;
        setMandates(mandateRows);
        setTargets(targetRows);
        setCapitalBudget(budget);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, []);

  const saveCapitalBudget = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await updateCapitalBudget({
        total_budget: String(form.get("total_budget")),
        currency: String(form.get("currency")),
        change_reason: String(form.get("change_reason")),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await createMandate({
        mandate_name: String(form.get("mandate_name")),
        status: "draft",
        purpose: String(form.get("purpose")),
        allocation_weight: form.get("allocation_weight") ? Number(form.get("allocation_weight")) / 100 : null,
        expected_return: form.get("expected_return") ? Number(form.get("expected_return")) / 100 : null,
        max_drawdown: form.get("max_drawdown") ? Number(form.get("max_drawdown")) / 100 : null,
        horizon_months: form.get("horizon_months") ? Number(form.get("horizon_months")) : null,
        benchmark_target_id: form.get("benchmark_target_id") ? Number(form.get("benchmark_target_id")) : null,
        benchmark_security_key: String(form.get("benchmark_security_key") || "") || null,
        review_cycle: reviewCycle || null,
        review_cycle_custom: reviewCycle === "other" ? String(form.get("review_cycle_custom") || "") || null : null,
        next_review_at: nextReviewAt || null,
        change_reason: "Initial version",
      });
      event.currentTarget.reset();
      setReviewCycle("");
      setNextReviewAt("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (mandate: CapitalAllocationMandate) => {
    if (!window.confirm(`投資枠「${mandate.mandate_name}」を削除しますか？\n全versionと配分も削除されます。`)) return;
    try {
      await deleteMandate(mandate.mandate_id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">投資枠</h1>
        <p className="mt-1 text-sm text-gray-600">総投資予算を投資目的別に配分し、制約・ベンチマーク・見直し予定を管理します。</p>
        {PUBLIC_DEMO_WRITES && <p className="mt-2 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">公開デモは共有環境です。入力した投資枠は他の閲覧者にも表示されます。1時間後に画面から非表示となり、定期処理で物理削除されます。サンプル投資枠と総投資予算は変更できません。</p>}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}

      <section className="rounded-lg border bg-white p-5">
        <h2 className="font-semibold">総投資予算</h2>
        <p className="mt-1 text-sm text-gray-600">各投資枠の予算額は、総投資予算 × 投資枠の予算割合で自動計算します。</p>
        {PUBLIC_READ_ONLY ? (
          <p className="mt-3 text-xl font-semibold">{money(capitalBudget?.total_budget ?? null, capitalBudget?.currency ?? null)}</p>
        ) : (
          <form key={capitalBudget?.capital_budget_version_id ?? "new"} onSubmit={saveCapitalBudget} className="mt-4 grid gap-3 md:grid-cols-4">
            <label className="text-sm"><span className="mb-1 block font-medium">投資予算総額</span><input name="total_budget" required type="number" min="0" step="1" defaultValue={capitalBudget?.total_budget ?? ""} className="w-full rounded border p-2" /></label>
            <label className="text-sm"><span className="mb-1 block font-medium">通貨</span><input name="currency" required maxLength={3} defaultValue={capitalBudget?.currency ?? "JPY"} className="w-full rounded border p-2" /></label>
            <label className="text-sm"><span className="mb-1 block font-medium">変更理由</span><input name="change_reason" required placeholder="例：入金に伴う増額" className="w-full rounded border p-2" /></label>
            <button className="self-end rounded bg-gray-800 px-4 py-2 text-sm text-white">予算を保存</button>
          </form>
        )}
      </section>

      {canCreateDemoMandates && (
        <form onSubmit={submit} className="grid gap-4 rounded-lg border bg-white p-5 md:grid-cols-2 lg:grid-cols-4">
          <h2 className="font-semibold md:col-span-2 lg:col-span-4">投資枠を新規作成</h2>
          <label className="text-sm"><span className="mb-1 block font-medium">投資枠名</span><input name="mandate_name" required placeholder="例：長期インデックス投資" className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">投資予算に対する割合</span><input name="allocation_weight" type="number" min="0" max="100" step="0.1" placeholder="例：60%" className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">期待リターン（年率）</span><input name="expected_return" type="number" min="-100" max="1000" step="0.1" placeholder="%" className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">許容最大ドローダウン</span><input name="max_drawdown" type="number" min="-100" max="0" step="0.1" placeholder="例：-40%" className="w-full rounded border p-2" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">想定投資期間（月）</span><input name="horizon_months" type="number" min="1" placeholder="例：120" className="w-full rounded border p-2" /></label>
          <div className="text-sm"><span className="mb-1 block font-medium">比較ベンチマーク</span><BenchmarkSecurityPicker key={`new-${mandates.length}`} targets={targets} catalogSelectable={!PUBLIC_READ_ONLY} /></div>
          <label className="text-sm"><span className="mb-1 block font-medium">見直し周期</span><select name="review_cycle" value={reviewCycle} onChange={(event) => { const cycle = event.target.value as ReviewCycle | ""; setReviewCycle(cycle); setNextReviewAt(suggestedReviewDate(cycle)); }} className="w-full rounded border p-2"><option value="">定期見直しなし</option>{Object.entries(REVIEW_CYCLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {reviewCycle === "other" && <label className="text-sm"><span className="mb-1 block font-medium">その他の見直し周期</span><input name="review_cycle_custom" required placeholder="例：決算発表後" className="w-full rounded border p-2" /></label>}
          <label className="text-sm"><span className="mb-1 block font-medium">次回見直し日</span><input name="next_review_at" type="date" min={today} value={nextReviewAt} onChange={(event) => setNextReviewAt(event.target.value)} className="w-full rounded border p-2" /><span className="mt-1 block text-xs text-gray-500">周期選択時に自動入力します。手動変更も可能です。</span></label>
          <label className="text-sm md:col-span-2 lg:col-span-3"><span className="mb-1 block font-medium">投資目的・運用方針</span><textarea name="purpose" required className="w-full rounded border p-2" /></label>
          <button disabled={saving} className="self-end rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{saving ? "保存中..." : "投資枠を作成"}</button>
        </form>
      )}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {mandates.map((mandate) => (
          <article key={mandate.mandate_id} className="rounded-lg border bg-white p-5">
            <div className="flex items-start justify-between gap-3"><Link href={`/mandates/${mandate.mandate_id}`} className="font-semibold hover:underline">{mandate.mandate_name}</Link><span className="text-xs uppercase text-gray-500">{mandate.status}</span></div>
            <p className="mt-1 font-mono text-xs text-gray-400">{mandate.mandate_key}</p>
            <p className="mt-2 text-sm text-gray-600">{mandate.purpose}</p>
            <p className="mt-4 text-xl font-semibold">{money(mandate.budget_amount, mandate.currency)}</p>
            <p className="mt-1 text-xs text-gray-500">投資予算割合：{mandate.allocation_weight == null ? "未設定" : `${(mandate.allocation_weight * 100).toFixed(1)}%`}</p>
            <p className="mt-2 text-xs text-gray-500">次回見直し日：{mandate.next_review_at ?? "未設定"}</p>
            <p className="mt-1 text-xs text-gray-500">ベンチマーク：{mandate.benchmark_target_name ?? "未設定"}</p>
            {PUBLIC_DEMO_WRITES && mandate.demo_expires_at && <p className="mt-2 text-xs text-amber-700">デモ入力・削除予定: {new Date(mandate.demo_expires_at).toLocaleString("ja-JP")}</p>}
            <div className="mt-3 flex items-center justify-between text-xs text-gray-500"><span>配分済み {(mandate.allocated_weight * 100).toFixed(1)}%</span><div className="flex items-center gap-3"><span>v{mandate.version_no}</span>{(!PUBLIC_READ_ONLY || (PUBLIC_DEMO_WRITES && mandate.demo_expires_at)) && <button type="button" onClick={() => void remove(mandate)} className="text-red-600 hover:underline">削除</button>}</div></div>
          </article>
        ))}
        {mandates.length === 0 && <p className="text-sm text-gray-500">投資枠はまだ登録されていません。</p>}
      </div>
    </div>
  );
}
