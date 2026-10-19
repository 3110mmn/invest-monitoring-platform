import { request } from "./http";

export type MetricKey = "revenue" | "operating_income" | "net_income";
export type ComparisonType = "forecast_variance" | "yoy" | "absolute" | "sign_transition";
export type Operator = "lt" | "lte" | "gt" | "gte" | "crosses_below" | "crosses_above";
export type ScopeType = "investment_target" | "mandate" | "theme" | "watchlist";
export type WatchlistStatus = "considering" | "monitoring" | "paused";
export type DecisionType = "investigate" | "review_allocation" | "buy" | "sell" | "hold" | "no_action";
export type ResponseDisposition = "accepted" | "overridden" | "deferred";
export type LogicOperator = "all" | "any";
export interface TriggerCondition {
  metric_key: MetricKey; comparison_type: ComparisonType; operator: Operator;
  threshold_value: string | number | null;
}

export interface TriggerDefinitions {
  definitions: Array<{
    key: string;
    label: string;
    metrics: Array<{ key: MetricKey; label: string }>;
    comparisons: Array<{ key: ComparisonType; label: string; operators: Operator[] }>;
  }>;
}
export interface TriggerRule {
  rule_id: number; rule_version_id: number; rule_key: string; version: number;
  scope_type: ScopeType; scope_label: string;
  target_id: number | null; target_key: string | null; target_name: string | null;
  mandate_id: number | null; theme_id: number | null; watchlist_status: WatchlistStatus | null;
  definition_key: string;
  logic_operator: LogicOperator; conditions: TriggerCondition[];
  metric_key: MetricKey; comparison_type: ComparisonType; operator: Operator;
  threshold_value: string | null; on_fire_decision: DecisionType;
  rationale: string; is_active: boolean;
  evaluation_count: number; response_count: number;
}
export interface TriggerEvaluation {
  evaluation_id: number; rule_version_id: number; rule_id: number;
  mode: "replay" | "live"; status: "fired" | "not_fired" | "unavailable";
  reason: string; observed_value: string | null; reference_value: string | null;
  as_of_date: string; evaluated_at: string; target_name: string; target_key: string;
  logic_operator: LogicOperator; conditions: TriggerCondition[];
  metric_key: MetricKey; comparison_type: ComparisonType; operator: Operator;
  threshold_value: string | null; evidence: Record<string, unknown>;
  on_fire_decision: DecisionType;
  response_count: number; latest_response_disposition: ResponseDisposition | null; next_review_on: string | null;
}
export interface TriggerResponse {
  response_id: number; disposition: ResponseDisposition; override_decision: DecisionType | null; rationale: string;
  created_at: string; next_review_on: string | null;
}

export const getTriggerDefinitions = () => request<TriggerDefinitions>("/triggers/definitions");
export const getTriggerRules = (includeInactive = false) =>
  request<TriggerRule[]>(`/triggers/rules${includeInactive ? "?include_inactive=true" : ""}`);
export type WorkState = "actionable" | "unhandled" | "deferred" | "settled" | "all";
export const getTriggerEvaluations = (workState: WorkState = "actionable") =>
  request<TriggerEvaluation[]>(
    `/triggers/evaluations${workState === "all" ? "" : `?work_state=${workState}`}`,
  );
export const saveTriggerRule = (data: {
  scope_type: ScopeType; target_id?: number | null; target_security_key?: string | null;
  mandate_id?: number | null;
  theme_id?: number | null; watchlist_status?: WatchlistStatus | null;
  logic_operator: LogicOperator; conditions: Array<{
    metric_key: MetricKey; comparison_type: ComparisonType;
    operator: Operator; threshold_value: number | null;
  }>; on_fire_decision: DecisionType;
  rationale: string; supersedes_id?: number;
}) => request<TriggerRule>("/triggers/rules", { method: "POST", body: JSON.stringify(data) });
export const disableTriggerRule = (id: number) =>
  request<void>(`/triggers/rules/${id}/disable`, { method: "POST" });
export const enableTriggerRule = (id: number) =>
  request<void>(`/triggers/rules/${id}/enable`, { method: "POST" });
export const deleteTriggerRule = (id: number) =>
  request<{ rule_id: number; deleted_versions: number; deleted_evaluations: number; deleted_responses: number }>(
    `/triggers/rules/${id}`, { method: "DELETE" },
  );
export const evaluateTriggerRule = (id: number, asOf: string, mode: "replay" | "live") =>
  request<{ evaluated: number; inserted: number; message: string }>(`/triggers/rules/${id}/evaluate`, {
    method: "POST", body: JSON.stringify({ mode, as_of_date: asOf }),
  });
export const getTriggerResponses = (id: number) =>
  request<TriggerResponse[]>(`/triggers/evaluations/${id}/responses`);
export const saveTriggerResponse = (id: number, data: {
  disposition: ResponseDisposition; override_decision: DecisionType | null;
  rationale: string; next_review_on: string | null;
}) => request<TriggerResponse>(`/triggers/evaluations/${id}/responses`, {
  method: "POST", body: JSON.stringify(data),
});

export const METRIC_LABELS: Record<MetricKey, string> = {
  revenue: "売上高", operating_income: "営業利益", net_income: "純利益",
};
export const COMPARISON_LABELS: Record<ComparisonType, string> = {
  forecast_variance: "期初予想比", yoy: "前年同期比", absolute: "実績値",
  sign_transition: "状態変化（黒字・赤字）",
};
export const OPERATOR_LABELS: Record<Operator, string> = {
  lt: "未満（<）", lte: "以下（≤）", gt: "超（>）", gte: "以上（≥）",
  crosses_below: "黒字から赤字", crosses_above: "赤字から黒字",
};
export const DECISION_LABELS: Record<DecisionType, string> = {
  investigate: "追加調査", review_allocation: "配分見直し", buy: "買い", sell: "売り",
  hold: "保有継続", no_action: "対応なし",
};
export const DISPOSITION_LABELS: Record<ResponseDisposition, string> = {
  accepted: "方針を承認", overridden: "方針を上書き", deferred: "判断を保留",
};
