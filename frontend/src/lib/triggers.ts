import { request } from "./http";

export type MetricKey = "revenue" | "operating_income" | "net_income";
export type ComparisonType = "forecast_variance" | "yoy" | "absolute" | "sign_transition";
export type Operator = "lte" | "gte" | "crosses_below" | "crosses_above";

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
  target_id: number; target_key: string; target_name: string; definition_key: string;
  metric_key: MetricKey; comparison_type: ComparisonType; operator: Operator;
  threshold_value: string | null; rationale: string; is_active: boolean;
}
export interface TriggerEvaluation {
  evaluation_id: number; rule_version_id: number; rule_id: number;
  mode: "replay" | "live"; status: "fired" | "not_fired" | "unavailable";
  reason: string; observed_value: string | null; reference_value: string | null;
  as_of_date: string; target_name: string; target_key: string;
  metric_key: MetricKey; comparison_type: ComparisonType; operator: Operator;
  threshold_value: string | null; evidence: Record<string, unknown>;
}
export interface TriggerResponse {
  response_id: number; response_type: ResponseType; rationale: string;
  created_at: string; next_review_on: string | null;
}
export type ResponseType = "continue" | "investigate" | "review_allocation" | "hold";

export const getTriggerDefinitions = () => request<TriggerDefinitions>("/triggers/definitions");
export const getTriggerRules = () => request<TriggerRule[]>("/triggers/rules");
export const getTriggerEvaluations = () => request<TriggerEvaluation[]>("/triggers/evaluations");
export const saveTriggerRule = (data: {
  target_id: number; metric_key: MetricKey; comparison_type: ComparisonType;
  operator: Operator; threshold_value: number | null; rationale: string; supersedes_id?: number;
}) => request<TriggerRule>("/triggers/rules", { method: "POST", body: JSON.stringify(data) });
export const disableTriggerRule = (id: number) =>
  request<void>(`/triggers/rules/${id}/disable`, { method: "POST" });
export const evaluateTriggerRule = (id: number, asOf: string, mode: "replay" | "live") =>
  request<{ evaluated: number; inserted: number; message: string }>(`/triggers/rules/${id}/evaluate`, {
    method: "POST", body: JSON.stringify({ mode, as_of_date: asOf }),
  });
export const getTriggerResponses = (id: number) =>
  request<TriggerResponse[]>(`/triggers/evaluations/${id}/responses`);
export const saveTriggerResponse = (id: number, data: {
  response_type: ResponseType; rationale: string; next_review_on: string | null;
}) => request<TriggerResponse>(`/triggers/evaluations/${id}/responses`, {
  method: "POST", body: JSON.stringify(data),
});

export const METRIC_LABELS: Record<MetricKey, string> = {
  revenue: "売上高", operating_income: "営業利益", net_income: "純利益",
};
export const COMPARISON_LABELS: Record<ComparisonType, string> = {
  forecast_variance: "期初予想比", yoy: "前年同期比", absolute: "実績値", sign_transition: "符号転換",
};
export const OPERATOR_LABELS: Record<Operator, string> = {
  lte: "以下", gte: "以上", crosses_below: "黒字から赤字", crosses_above: "赤字から黒字",
};
export const RESPONSE_LABELS: Record<ResponseType, string> = {
  continue: "継続", investigate: "追加調査", review_allocation: "配分見直しを検討", hold: "保留",
};
