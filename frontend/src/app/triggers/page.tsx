"use client";

import { useEffect, useMemo, useState } from "react";
import { getInvestmentTargets, getMandates, getThemes, searchSecurities } from "@/lib/api";
import { PUBLIC_DEMO_WRITES, PUBLIC_READ_ONLY } from "@/lib/runtime";
import { todayInJapan } from "@/lib/review-cycle";
import {
  COMPARISON_LABELS, DECISION_LABELS, deleteTriggerRule, disableTriggerRule, DISPOSITION_LABELS, enableTriggerRule, evaluateTriggerRule, getTriggerDefinitions,
  getTriggerEvaluations, getTriggerResponses, getTriggerRules, METRIC_LABELS,
  OPERATOR_LABELS, saveTriggerResponse, saveTriggerRule,
  type ComparisonType, type DecisionType, type LogicOperator, type MetricKey, type Operator, type ResponseDisposition, type ScopeType,
  type TriggerCondition,
  type TriggerEvaluation, type TriggerResponse, type TriggerRule, type WatchlistStatus,
  type WorkState,
} from "@/lib/triggers";
import type { CapitalAllocationMandate, InvestmentTarget, Security, Theme } from "@/types";

const editable = !PUBLIC_READ_ONLY && !PUBLIC_DEMO_WRITES;
const field = "rounded border p-2 text-sm";
const button = "rounded bg-blue-700 px-3 py-2 text-white disabled:opacity-50";
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
// 参照値の正体は比較軸で違う。期初予想比なら会社予想、前年比と符号転換なら前年実績を
// 指すため、同じ `reference_missing` でも説明を変える。固定文言だと前年比の判定にも
// 「期初予想がありません」と出る。
const referenceLabels: Record<string, string> = {
  forecast_variance: "期初予想",
  yoy: "前年実績",
  sign_transition: "前年実績",
};

// 「今やること」を既定にする。保留のうち再確認日が未来のものは作業キューから外し、
// 切り替えて見る。判定履歴の一覧ではなく、対応待ちの案件一覧として扱う。
const workStateLabels: Record<string, string> = {
  actionable: "要対応（未対応・再確認）",
  unhandled: "未対応",
  deferred: "保留中",
  settled: "対応済み",
  all: "すべて",
};

const reasonLabels: Record<string, string> = {
  basis_unknown: "連結区分または会計基準が不明です",
  current_missing: "通期実績がありません",
  unsupported_period: "約1年の通期決算ではありません",
  period_unknown: "決算期間が不明です",
  not_available_yet: "評価基準日時点では未公表です",
  nonfinite_value: "有限の評価値を計算できません",
  unsupported_operator: "未対応の演算子です",
  condition_unavailable: "一部の条件に必要なデータがなく、Rule全体を判定できません",
};

// 取得期間の外にある開示は取りに行けない。J-Quants Freeは2年窓のため、各銘柄で最も
// 古い年度は比較対象を持てず、必ずここへ落ちる。データの不備と区別して伝える。
const reasonText = (reason: string, comparisonType: string) => {
  const reference = referenceLabels[comparisonType] ?? "比較対象";
  if (reason === "reference_missing") return `比較する${reference}が取得範囲にありません`;
  if (reason === "reference_nonpositive") return `${reference}がゼロまたは赤字のため比率を計算できません`;
  return reasonLabels[reason] ?? reason;
};

// 同じRule version・イベントは一度しか保存しないため、2回目以降の `inserted` は0になる。
// 「新規0件」とだけ出すと失敗に見えるので、既に評価済みであることを明示する。
const evaluationMessage = (result: { evaluated: number; inserted: number; message: string }) => {
  if (result.evaluated === 0) return result.message;
  if (result.inserted === 0) return `評価済み：対象${result.evaluated}件はすべて記録済みです`;
  return `評価しました：対象${result.evaluated}件のうち${result.inserted}件を記録しました`;
};

const evidenceText = (evidence: Record<string, unknown>, key: string) => {
  const value = evidence[key];
  return value == null ? "—" : String(value);
};

const recordValue = (value: unknown): Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};

const statusText = (value: unknown) => value === "fired" ? "条件該当"
  : value === "not_fired" ? "条件非該当" : "判定不能";

const formatDateTime = (value: string) => new Intl.DateTimeFormat("ja-JP", {
  dateStyle: "short", timeStyle: "short", timeZone: "Asia/Tokyo",
}).format(new Date(value));

type ConditionDraft = Omit<TriggerCondition, "threshold_value"> & { threshold_value: string };
const defaultCondition = (): ConditionDraft => ({
  metric_key: "operating_income", comparison_type: "forecast_variance",
  operator: "lte", threshold_value: "-20",
});
const operatorsFor = (comparison: ComparisonType): Operator[] => comparison === "sign_transition"
  ? ["crosses_below", "crosses_above"] : ["lt", "lte", "gt", "gte"];
const conditionText = (condition: TriggerCondition) =>
  `${METRIC_LABELS[condition.metric_key]} · ${COMPARISON_LABELS[condition.comparison_type]} · ${OPERATOR_LABELS[condition.operator]} ${condition.threshold_value ?? ""}`;

function EvaluationCard({ item }: { item: TriggerEvaluation }) {
  const [history, setHistory] = useState<TriggerResponse[]>([]);
  const [disposition, setDisposition] = useState<ResponseDisposition>("accepted");
  const [overrideDecision, setOverrideDecision] = useState<DecisionType>("no_action");
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
  const memberEvidence = Array.isArray(item.evidence.conditions) ? item.evidence.conditions : [];
  return <article id={`evaluation-${item.evaluation_id}`} className="space-y-3 rounded border bg-white p-4">
    <h3 className="font-semibold">{item.target_name}（{item.target_key}）</h3>
    <p>{item.conditions.map(conditionText).join(item.logic_operator === "all" ? " かつ " : " または ")}</p>
    <p>{item.status === "fired" ? "条件該当" : item.status === "not_fired" ? "条件非該当" : "判定不能"}</p>
    <p>発火時の判断方針：{DECISION_LABELS[item.on_fire_decision]}（人間の確認が必要）</p>
    {item.conditions.length === 1 && <p>評価値：{format(item.observed_value)} ／ 参照値：{format(item.reference_value)} ／ 閾値：{format(item.threshold_value)}</p>}
    {item.conditions.length > 1 && <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr className="text-left"><th className="py-1 pr-3">条件</th><th className="py-1 pr-3">判定</th><th className="py-1 pr-3">評価値</th><th className="py-1 pr-3">参照値</th></tr></thead>
        <tbody>{item.conditions.map((condition, index) => {
          const member = recordValue(memberEvidence[index]);
          return <tr key={index} className="border-t">
            <td className="py-1 pr-3">{conditionText(condition)}</td>
            <td className="py-1 pr-3">{statusText(member.status)}</td>
            <td className="py-1 pr-3">{member.observed_value == null ? "—" : Number(member.observed_value).toLocaleString("ja-JP")}</td>
            <td className="py-1 pr-3">{member.reference_value == null ? "—" : Number(member.reference_value).toLocaleString("ja-JP")}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
    {item.status === "unavailable" && <p className="text-sm text-gray-600">
      判定できない理由：{reasonText(item.reason, item.comparison_type)}
    </p>}
    <dl className="grid gap-2 text-sm md:grid-cols-2">
      <div><dt className="font-medium">実績開示</dt><dd>{evidenceText(item.evidence, "actual_disclosure_number")}</dd></div>
      {item.conditions.length === 1 && <div><dt className="font-medium">参照開示</dt><dd>{evidenceText(item.evidence, "prior_disclosure_number")}</dd></div>}
      {item.conditions.length === 1 && <div><dt className="font-medium">実績値</dt><dd>{evidenceText(item.evidence, "current_value")}</dd></div>}
      <div><dt className="font-medium">情報開示日</dt><dd>{evidenceText(item.evidence, "actual_disclosed_date")}</dd></div>
      <div><dt className="font-medium">判定基準日</dt><dd>{item.as_of_date}</dd></div>
      <div><dt className="font-medium">発火・評価日時</dt><dd>{formatDateTime(item.evaluated_at)}</dd></div>
    </dl>
    <details><summary className="cursor-pointer">判定根拠・来歴</summary>
      <pre className="overflow-auto text-xs">{JSON.stringify(item.evidence, null, 2)}</pre>
    </details>
    {history.map((row) => <p key={row.response_id} className="rounded bg-gray-50 p-2 text-sm">
      対応：{DISPOSITION_LABELS[row.disposition]}
      {row.override_decision && ` → ${DECISION_LABELS[row.override_decision]}`} — {row.rationale}
      {row.next_review_on && ` ／ 次回確認：${row.next_review_on}`}
    </p>)}
    {editable && item.status === "fired" && <form className="flex flex-wrap gap-2" onSubmit={async (event) => {
      event.preventDefault(); setError("");
      try {
        await saveTriggerResponse(item.evaluation_id, {
          disposition, override_decision: disposition === "overridden" ? overrideDecision : null,
          rationale: responseRationale,
          next_review_on: disposition === "deferred" ? (nextDate || null) : null,
        });
        setHistory(await getTriggerResponses(item.evaluation_id)); setResponseRationale("");
      } catch (cause) { setError(errorText(cause)); }
    }}>
      <label>判断 <select className={field} value={disposition} onChange={(event) => setDisposition(event.target.value as ResponseDisposition)}>
        {(Object.keys(DISPOSITION_LABELS) as ResponseDisposition[])
          .map((value) => <option key={value} value={value}>{DISPOSITION_LABELS[value]}</option>)}
      </select></label>
      {disposition === "overridden" && <label>上書き後の方針 <select className={field} value={overrideDecision} onChange={(event) => setOverrideDecision(event.target.value as DecisionType)}>
        {(Object.keys(DECISION_LABELS) as DecisionType[])
          .map((value) => <option key={value} value={value}>{DECISION_LABELS[value]}</option>)}
      </select></label>}
      {disposition === "deferred" && <label>次回確認日 <input required type="date" className={field} value={nextDate} onChange={(event) => setNextDate(event.target.value)} /></label>}
      <label className="w-full">対応理由 <textarea required className={`${field} w-full`} value={responseRationale} onChange={(event) => setResponseRationale(event.target.value)} /></label>
      <button className={button}>記録を追加</button>
    </form>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
  </article>;
}

export default function TriggersPage() {
  const [targets, setTargets] = useState<InvestmentTarget[]>([]);
  const [mandates, setMandates] = useState<CapitalAllocationMandate[]>([]);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [rules, setRules] = useState<TriggerRule[]>([]);
  const [evaluations, setEvaluations] = useState<TriggerEvaluation[]>([]);
  const [scopeType, setScopeType] = useState<ScopeType>("investment_target");
  const [targetId, setTargetId] = useState("");
  const [securityQuery, setSecurityQuery] = useState("");
  const [securityResults, setSecurityResults] = useState<Security[]>([]);
  const [selectedSecurity, setSelectedSecurity] = useState<Security | null>(null);
  const [mandateId, setMandateId] = useState("");
  const [themeId, setThemeId] = useState("");
  const [watchlistStatus, setWatchlistStatus] = useState<"all" | WatchlistStatus>("all");
  const [logicOperator, setLogicOperator] = useState<LogicOperator>("all");
  const [conditions, setConditions] = useState<ConditionDraft[]>([defaultCondition()]);
  const [onFireDecision, setOnFireDecision] = useState<DecisionType>("investigate");
  const [rationale, setRationale] = useState("");
  const [supersedes, setSupersedes] = useState<number>();

  const [workState, setWorkState] = useState<WorkState>("actionable");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [showInactive, setShowInactive] = useState(false);

  async function reload() {
    const [nextRules, nextEvaluations] = await Promise.all([
      getTriggerRules(showInactive), getTriggerEvaluations(workState),
    ]);
    setRules(nextRules); setEvaluations(nextEvaluations);
  }
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getInvestmentTargets(), getMandates(), getThemes(true), getTriggerRules(showInactive),
      getTriggerEvaluations(workState), getTriggerDefinitions(),
    ])
      .then(([nextTargets, nextMandates, nextThemes, nextRules, nextEvaluations, catalog]) => {
        if (cancelled) return;
        setTargets(nextTargets); setMandates(nextMandates); setThemes(nextThemes);
        setRules(nextRules); setEvaluations(nextEvaluations);
        void catalog;
      })
      .catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    return () => { cancelled = true; };
  }, [showInactive, workState]);
  useEffect(() => {
    const query = securityQuery.trim();
    if (query.length < 2 || scopeType !== "investment_target" || supersedes) {
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchSecurities(query)
        .then((rows) => { if (!cancelled) setSecurityResults(rows.filter((row) => row.target_key)); })
        .catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [scopeType, securityQuery, supersedes]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); await reload(); } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  function updateCondition(index: number, patch: Partial<ConditionDraft>) {
    setConditions((current) => current.map((condition, position) =>
      position === index ? { ...condition, ...patch } : condition));
  }
  function selectComparison(index: number, value: ComparisonType) {
    updateCondition(index, {
      comparison_type: value,
      operator: operatorsFor(value)[0],
      threshold_value: value === "sign_transition" ? "" : "-20",
    });
  }
  return <div className="space-y-6">
    <h1 className="text-2xl font-bold">トリガー</h1>
    <p>継続監視する条件、発火時の判断方針、判定結果と人間の確認を一元管理します。売買実績は証券会社データから別途取り込みます。</p>
    <p className="text-sm text-gray-600">期初予想比は、前期通期決算で最初に公表された翌期会社予想と、当期最初の通期実績を比較します。約1年の通期だけが対象です。</p>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {message && <p role="status">{message}</p>}
    {editable && <form className="space-y-3 rounded border bg-white p-4" onSubmit={(event) => {
      event.preventDefault(); void run(async () => {
        await saveTriggerRule({
          scope_type: scopeType,
          target_id: scopeType === "investment_target" && !selectedSecurity ? Number(targetId) : null,
          target_security_key: scopeType === "investment_target" ? selectedSecurity?.security_key ?? null : null,
          mandate_id: scopeType === "mandate" ? Number(mandateId) : null,
          theme_id: scopeType === "theme" ? Number(themeId) : null,
          watchlist_status: scopeType === "watchlist" && watchlistStatus !== "all" ? watchlistStatus : null,
          logic_operator: logicOperator,
          conditions: conditions.map((condition) => ({
            metric_key: condition.metric_key,
            comparison_type: condition.comparison_type,
            operator: condition.operator,
            threshold_value: condition.comparison_type === "sign_transition"
              ? null : Number(condition.threshold_value),
          })),
          on_fire_decision: onFireDecision, rationale, supersedes_id: supersedes,
        });
        setSupersedes(undefined); setRationale(""); setSelectedSecurity(null);
        setSecurityQuery(""); setSecurityResults([]); setMessage("ルールを保存しました");
      });
    }}>
      <h2 className="font-semibold">{supersedes ? "ルールを改訂" : "ルールを設定"}</h2>
      <label className="block">適用単位 <select disabled={!!supersedes} className={field} value={scopeType} onChange={(event) => {
        setScopeType(event.target.value as ScopeType); setSelectedSecurity(null);
        setSecurityQuery(""); setSecurityResults([]); setTargetId("");
      }}>
        <option value="investment_target">投資対象</option><option value="mandate">投資枠</option>
        <option value="theme">テーマ</option><option value="watchlist">ウォッチリスト</option>
      </select></label>
      {scopeType === "investment_target" && <div className="space-y-2">
        <label className="block">投資対象を全銘柄から検索
          <input disabled={!!supersedes} className={`${field} ml-2 w-full max-w-2xl`} value={securityQuery}
            placeholder="コード・銘柄名で検索" onChange={(event) => {
              setSecurityQuery(event.target.value); setSelectedSecurity(null); setSecurityResults([]); setTargetId("");
            }} />
        </label>
        {selectedSecurity && <p className="text-sm text-blue-700">選択中：{selectedSecurity.company_name}（{selectedSecurity.target_key}）</p>}
        {securityResults.length > 0 && !selectedSecurity && <ul className="max-h-48 max-w-2xl overflow-y-auto rounded border bg-white text-sm">
          {securityResults.map((security) => <li key={security.security_key}>
            <button type="button" className="w-full px-2 py-1 text-left hover:bg-gray-100" onClick={() => {
              setSelectedSecurity(security); setTargetId(""); setSecurityResults([]);
            }}>{security.company_name}（{security.target_key}）</button>
          </li>)}
        </ul>}
        <label className="block">または登録済み投資対象から選択 <select required={!selectedSecurity} disabled={!!supersedes || !!selectedSecurity} className={field} value={targetId} onChange={(event) => {
          setTargetId(event.target.value); setSelectedSecurity(null); setSecurityQuery(""); setSecurityResults([]);
        }}>
          <option value="">選択してください</option>{targets.map((item) => <option key={item.target_id} value={item.target_id}>{item.target_name}（{item.target_key}）</option>)}
        </select></label>
      </div>}
      {scopeType === "mandate" && <label className="block">投資枠 <select required disabled={!!supersedes} className={field} value={mandateId} onChange={(event) => setMandateId(event.target.value)}>
        <option value="">選択してください</option>{mandates.map((item) => <option key={item.mandate_id} value={item.mandate_id}>{item.mandate_name}</option>)}
      </select></label>}
      {scopeType === "theme" && <label className="block">テーマ <select required disabled={!!supersedes} className={field} value={themeId} onChange={(event) => setThemeId(event.target.value)}>
        <option value="">選択してください</option>{themes.map((item) => <option key={item.theme_id} value={item.theme_id}>{item.theme_name}</option>)}
      </select></label>}
      {scopeType === "watchlist" && <label className="block">ウォッチリスト範囲 <select disabled={!!supersedes} className={field} value={watchlistStatus} onChange={(event) => setWatchlistStatus(event.target.value as "all" | WatchlistStatus)}>
        <option value="all">登録銘柄すべて</option><option value="considering">検討中</option>
        <option value="monitoring">監視中</option><option value="paused">監視停止</option>
      </select></label>}
      <label className="block">条件の結合 <select className={field} value={logicOperator} onChange={(event) => setLogicOperator(event.target.value as LogicOperator)}>
        <option value="all">すべて満たす（AND）</option><option value="any">いずれかを満たす（OR）</option>
      </select></label>
      <fieldset className="space-y-2 rounded border p-3">
        <legend className="px-1 font-medium">監視条件</legend>
        {conditions.map((condition, index) => {
          const crossing = condition.comparison_type === "sign_transition";
          return <div key={index} className="flex flex-wrap items-end gap-3">
            <label>指標 <select className={field} value={condition.metric_key} onChange={(event) => updateCondition(index, { metric_key: event.target.value as MetricKey })}>
              {Object.entries(METRIC_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            <label>比較 <select className={field} value={condition.comparison_type} onChange={(event) => selectComparison(index, event.target.value as ComparisonType)}>
              {Object.entries(COMPARISON_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            <label>条件 <select className={field} value={condition.operator} onChange={(event) => updateCondition(index, { operator: event.target.value as Operator })}>
              {operatorsFor(condition.comparison_type).map((value) => <option key={value} value={value}>{OPERATOR_LABELS[value]}</option>)}
            </select></label>
            {!crossing && <label>閾値 <input required type="number" step="0.01" className={field} value={condition.threshold_value} onChange={(event) => updateCondition(index, { threshold_value: event.target.value })} /></label>}
            {conditions.length > 1 && <button type="button" className="px-2 py-2 text-red-700" aria-label={`条件${index + 1}を削除`} onClick={() => setConditions((current) => current.filter((_, position) => position !== index))}>削除</button>}
          </div>;
        })}
        <button type="button" className="text-blue-700" onClick={() => setConditions((current) => [...current, defaultCondition()])}>＋ 条件を追加</button>
      </fieldset>
      <div className="flex flex-wrap gap-3">
        <label>発火時の判断方針 <select className={field} value={onFireDecision} onChange={(event) => setOnFireDecision(event.target.value as DecisionType)}>
          {(Object.keys(DECISION_LABELS) as DecisionType[])
            .map((value) => <option key={value} value={value}>{DECISION_LABELS[value]}</option>)}
        </select></label>
      </div>
      <label className="block">設定・改訂理由 <textarea required maxLength={2000} className={`${field} w-full`} value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
      <button disabled={busy} className={button}>保存</button>
    </form>}
    <section className="space-y-3">
      <h2 className="font-semibold">登録ルール</h2>
      {editable && <div className="flex flex-wrap gap-3">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showInactive} onChange={(event) => setShowInactive(event.target.checked)} />
          停止中も表示
        </label>
      </div>}
      {rules.map((item) => <div key={item.rule_id} className="rounded border bg-white p-3">
        <p className={item.is_active ? "" : "text-slate-400"}>
          {!item.is_active && <span className="mr-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600">停止中</span>}
          {item.scope_label} · {item.conditions.map(conditionText).join(item.logic_operator === "all" ? " かつ " : " または ")} · v{item.version}
        </p>
        <p className="text-sm">発火時：{DECISION_LABELS[item.on_fire_decision]}（確認あり）</p>
        <p className="text-sm">{item.rationale}</p>
        {editable && item.is_active && <div className="mt-2 flex gap-3">
          <button disabled={busy} className={button} onClick={() => void run(async () => {
            // 画面からは実運用の当日評価だけを行う。過去再生はRuleの閾値を校正する
            // 分析処理で、人間が対応すべき案件を生まない。CLIの replay_trigger で
            // DBへ書かずに確かめる。
            const result = await evaluateTriggerRule(item.rule_id, todayInJapan(), "live");
            setMessage(evaluationMessage(result));
          })}>評価する</button>
          <button onClick={() => {
            setSupersedes(item.rule_id); setScopeType(item.scope_type);
            setTargetId(item.target_id == null ? "" : String(item.target_id));
            setSelectedSecurity(null); setSecurityQuery(""); setSecurityResults([]);
            setMandateId(item.mandate_id == null ? "" : String(item.mandate_id));
            setThemeId(item.theme_id == null ? "" : String(item.theme_id));
            setWatchlistStatus(item.watchlist_status ?? "all"); setLogicOperator(item.logic_operator);
            setConditions(item.conditions.map((condition) => ({ ...condition, threshold_value: condition.threshold_value == null ? "" : String(condition.threshold_value) })));
            setOnFireDecision(item.on_fire_decision);
            setRationale(item.rationale);
          }}>改訂</button>
          <button onClick={() => void run(async () => { await disableTriggerRule(item.rule_id); })}>停止</button>
        </div>}
        {editable && !item.is_active && <div className="mt-2 flex gap-3">
          <button disabled={busy} className={button} onClick={() => void run(async () => { await enableTriggerRule(item.rule_id); })}>再開</button>
          <button disabled={busy} className="rounded border border-red-700 px-3 py-2 text-red-700" onClick={() => {
            const confirmed = window.confirm(
              `このルールを完全に削除します。Version ${item.version}件、判定 ${item.evaluation_count}件、対応履歴 ${item.response_count}件も削除され、元に戻せません。`,
            );
            if (!confirmed) return;
            void run(async () => {
              const result = await deleteTriggerRule(item.rule_id);
              setMessage(`ルールと判定${result.deleted_evaluations}件・対応履歴${result.deleted_responses}件を削除しました`);
            });
          }}>完全に削除</button>
        </div>}
      </div>)}
      {!rules.length && <p>ルール未登録です。</p>}
    </section>
    <section className="space-y-3">
      <h2 className="font-semibold">判定・対応</h2>
      <label>表示 <select className={field} value={workState} onChange={(event) => setWorkState(event.target.value as WorkState)}>
        {Object.entries(workStateLabels).map(([value, label]) =>
          <option key={value} value={value}>{label}</option>)}
      </select></label>
      <p className="text-sm text-gray-600">
        実運用の判定だけを表示します。過去再生は閾値の校正に使うもので、ここには出しません。
      </p>
      {evaluations.map((item) => <EvaluationCard key={item.evaluation_id} item={item} />)}
      {!evaluations.length && <p>{workStateLabels[workState]}の案件はありません。</p>}
    </section>
  </div>;
}
