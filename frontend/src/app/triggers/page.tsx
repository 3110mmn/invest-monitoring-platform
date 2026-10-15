"use client";

import { useEffect, useMemo, useState } from "react";
import { getInvestmentTargets } from "@/lib/api";
import { PUBLIC_DEMO_WRITES, PUBLIC_READ_ONLY } from "@/lib/runtime";
import { todayInJapan } from "@/lib/review-cycle";
import {
  COMPARISON_LABELS, disableTriggerRule, evaluateTriggerRule, getTriggerDefinitions,
  getTriggerEvaluations, getTriggerResponses, getTriggerRules, METRIC_LABELS,
  OPERATOR_LABELS, RESPONSE_LABELS, saveTriggerResponse, saveTriggerRule,
  type ComparisonType, type MetricKey, type Operator, type ResponseType, type TriggerEvaluation,
  type TriggerResponse, type TriggerRule,
} from "@/lib/triggers";
import type { InvestmentTarget } from "@/types";

const editable = !PUBLIC_READ_ONLY && !PUBLIC_DEMO_WRITES;
const field = "rounded border p-2 text-sm";
const button = "rounded bg-blue-700 px-3 py-2 text-white disabled:opacity-50";
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

function EvaluationCard({ item }: { item: TriggerEvaluation }) {
  const [history, setHistory] = useState<TriggerResponse[]>([]);
  const [responseType, setResponseType] = useState<ResponseType>("continue");
  const [responseRationale, setResponseRationale] = useState("");
  const [nextDate, setNextDate] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    getTriggerResponses(item.evaluation_id)
      .then((rows) => { if (!cancelled) setHistory(rows); })
      .catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    return () => { cancelled = true; };
  }, [item.evaluation_id]);
  const format = (value: string | null) => value == null ? "—" : Number(value).toLocaleString("ja-JP");
  return <article id={`evaluation-${item.evaluation_id}`} className="space-y-3 rounded border bg-white p-4">
    <h3 className="font-semibold">{item.target_name}（{item.target_key}）</h3>
    <p>{METRIC_LABELS[item.metric_key]} · {COMPARISON_LABELS[item.comparison_type]} · {OPERATOR_LABELS[item.operator]}</p>
    <p>{item.mode === "replay" ? "過去再生" : "実運用"} ／ {
      item.status === "fired" ? "条件該当" : item.status === "not_fired" ? "条件非該当" : "判定不能"
    }</p>
    <p>評価値：{format(item.observed_value)} ／ 参照値：{format(item.reference_value)} ／ 閾値：{format(item.threshold_value)}</p>
    <p className="text-sm text-gray-600">判定理由：{item.reason}</p>
    <details><summary className="cursor-pointer">判定根拠・来歴</summary>
      <pre className="overflow-auto text-xs">{JSON.stringify(item.evidence, null, 2)}</pre>
    </details>
    {history.map((row) => <p key={row.response_id} className="rounded bg-gray-50 p-2 text-sm">
      対応：{RESPONSE_LABELS[row.response_type]} — {row.rationale}
      {row.next_review_on && ` ／ 次回確認：${row.next_review_on}`}
    </p>)}
    {editable && item.status === "fired" && <form className="flex flex-wrap gap-2" onSubmit={async (event) => {
      event.preventDefault(); setError("");
      try {
        await saveTriggerResponse(item.evaluation_id, {
          response_type: responseType, rationale: responseRationale, next_review_on: nextDate || null,
        });
        setHistory(await getTriggerResponses(item.evaluation_id)); setResponseRationale("");
      } catch (cause) { setError(errorText(cause)); }
    }}>
      <label>対応 <select className={field} value={responseType} onChange={(event) => setResponseType(event.target.value as ResponseType)}>
        {(Object.keys(RESPONSE_LABELS) as ResponseType[])
          .map((value) => <option key={value} value={value}>{RESPONSE_LABELS[value]}</option>)}
      </select></label>
      <label>次回確認日 <input type="date" className={field} value={nextDate} onChange={(event) => setNextDate(event.target.value)} /></label>
      <label className="w-full">対応理由 <textarea required className={`${field} w-full`} value={responseRationale} onChange={(event) => setResponseRationale(event.target.value)} /></label>
      <button className={button}>記録を追加</button>
    </form>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
  </article>;
}

export default function TriggersPage() {
  const [targets, setTargets] = useState<InvestmentTarget[]>([]);
  const [rules, setRules] = useState<TriggerRule[]>([]);
  const [evaluations, setEvaluations] = useState<TriggerEvaluation[]>([]);
  const [targetId, setTargetId] = useState("");
  const [metric, setMetric] = useState<MetricKey>("operating_income");
  const [comparison, setComparison] = useState<ComparisonType>("forecast_variance");
  const [operator, setOperator] = useState<Operator>("lte");
  const [threshold, setThreshold] = useState("-10");
  const [rationale, setRationale] = useState("");
  const [supersedes, setSupersedes] = useState<number>();
  const [asOf, setAsOf] = useState(todayInJapan());
  const [mode, setMode] = useState<"replay" | "live">("replay");
  const [filter, setFilter] = useState("fired");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [allowedOperators, setAllowedOperators] = useState<Operator[]>(["lte", "gte"]);
  const crossing = comparison === "sign_transition";

  async function reload() {
    const [nextRules, nextEvaluations] = await Promise.all([getTriggerRules(), getTriggerEvaluations()]);
    setRules(nextRules); setEvaluations(nextEvaluations);
  }
  useEffect(() => {
    let cancelled = false;
    Promise.all([getInvestmentTargets(), getTriggerRules(), getTriggerEvaluations(), getTriggerDefinitions()])
      .then(([nextTargets, nextRules, nextEvaluations, catalog]) => {
        if (cancelled) return;
        setTargets(nextTargets); setRules(nextRules); setEvaluations(nextEvaluations);
        const selected = catalog.definitions[0]?.comparisons.find((item) => item.key === comparison);
        if (selected) setAllowedOperators(selected.operators);
      })
      .catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    return () => { cancelled = true; };
  }, [comparison]);
  const visible = useMemo(
    () => evaluations.filter((item) => filter === "all" || item.status === filter),
    [evaluations, filter],
  );
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); await reload(); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  function selectComparison(value: ComparisonType) {
    setComparison(value);
    const operators: Operator[] = value === "sign_transition" ? ["crosses_below", "crosses_above"] : ["lte", "gte"];
    setAllowedOperators(operators); setOperator(operators[0]);
  }
  return <div className="space-y-6">
    <h1 className="text-2xl font-bold">トリガー</h1>
    <p>継続監視する条件、判定結果、人間の対応を一元管理します。条件該当は売買指示ではなく、見直し要求です。</p>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {message && <p role="status">{message}</p>}
    {editable && <form className="space-y-3 rounded border bg-white p-4" onSubmit={(event) => {
      event.preventDefault(); void run(async () => {
        await saveTriggerRule({
          target_id: Number(targetId), metric_key: metric, comparison_type: comparison,
          operator, threshold_value: crossing ? null : Number(threshold), rationale, supersedes_id: supersedes,
        });
        setSupersedes(undefined); setRationale(""); setMessage("ルールを保存しました");
      });
    }}>
      <h2 className="font-semibold">{supersedes ? "ルールを改訂" : "ルールを設定"}</h2>
      <label className="block">投資対象 <select required disabled={!!supersedes} className={field} value={targetId} onChange={(event) => setTargetId(event.target.value)}>
        <option value="">選択してください</option>{targets.map((item) => <option key={item.target_id} value={item.target_id}>{item.target_name}（{item.target_key}）</option>)}
      </select></label>
      <div className="flex flex-wrap gap-3">
        <label>指標 <select className={field} value={metric} onChange={(event) => setMetric(event.target.value as MetricKey)}>
          {Object.entries(METRIC_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>比較 <select className={field} value={comparison} onChange={(event) => selectComparison(event.target.value as ComparisonType)}>
          {Object.entries(COMPARISON_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>条件 <select className={field} value={operator} onChange={(event) => setOperator(event.target.value as Operator)}>
          {allowedOperators.map((value) => <option key={value} value={value}>{OPERATOR_LABELS[value]}</option>)}
        </select></label>
        {!crossing && <label>閾値 <input required type="number" step="0.01" className={field} value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>}
      </div>
      <label className="block">設定・改訂理由 <textarea required maxLength={2000} className={`${field} w-full`} value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
      <button disabled={busy} className={button}>保存</button>
    </form>}
    <section className="space-y-3">
      <h2 className="font-semibold">登録ルール</h2>
      {editable && <div className="flex flex-wrap gap-3">
        <label>実行区分 <select className={field} value={mode} onChange={(event) => { setMode(event.target.value as "replay" | "live"); setAsOf(todayInJapan()); }}>
          <option value="replay">過去再生</option><option value="live">実運用</option>
        </select></label>
        <label>評価基準日 <input type="date" disabled={mode === "live"} max={todayInJapan()} className={field} value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label>
      </div>}
      {rules.map((item) => <div key={item.rule_id} className="rounded border bg-white p-3">
        <p>{item.target_name} · {METRIC_LABELS[item.metric_key]} · {COMPARISON_LABELS[item.comparison_type]} · {OPERATOR_LABELS[item.operator]} {item.threshold_value ?? ""} · v{item.version}</p>
        <p className="text-sm">{item.rationale}</p>
        {editable && item.is_active && <div className="mt-2 flex gap-3">
          <button disabled={busy} className={button} onClick={() => void run(async () => {
            const result = await evaluateTriggerRule(item.rule_id, asOf, mode);
            setMessage(`${result.message}：対象${result.evaluated}件、新規${result.inserted}件`);
          })}>評価する</button>
          <button onClick={() => {
            setSupersedes(item.rule_id); setTargetId(String(item.target_id)); setMetric(item.metric_key);
            selectComparison(item.comparison_type); setOperator(item.operator);
            setThreshold(item.threshold_value ?? ""); setRationale(item.rationale);
          }}>改訂</button>
          <button onClick={() => void run(async () => { await disableTriggerRule(item.rule_id); })}>停止</button>
        </div>}
      </div>)}
      {!rules.length && <p>ルール未登録です。</p>}
    </section>
    <section className="space-y-3">
      <h2 className="font-semibold">判定・対応</h2>
      <label>表示 <select className={field} value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="fired">発火</option><option value="not_fired">非発火</option><option value="unavailable">判定不能</option><option value="all">すべて</option>
      </select></label>
      {visible.map((item) => <EvaluationCard key={item.evaluation_id} item={item} />)}
      {!visible.length && <p>該当する判定履歴はありません。</p>}
    </section>
  </div>;
}
