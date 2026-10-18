"""Contracts shared by all operational trigger definitions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MetricKey = Literal["revenue", "operating_income", "net_income"]
ComparisonType = Literal["forecast_variance", "yoy", "absolute", "sign_transition"]
Operator = Literal["lt", "lte", "gt", "gte", "crosses_below", "crosses_above"]
ScopeType = Literal["investment_target", "mandate", "theme", "watchlist"]
WatchlistStatus = Literal["considering", "monitoring", "paused"]
DecisionType = Literal["investigate", "review_allocation", "buy", "sell", "hold", "no_action"]
ResponseDisposition = Literal["accepted", "overridden", "deferred"]
LogicOperator = Literal["all", "any"]


class RuleCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_key: MetricKey
    comparison_type: ComparisonType
    operator: Operator
    threshold_value: Decimal | None = Field(default=None, max_digits=20, decimal_places=4)

    @model_validator(mode="after")
    def validate_condition(self) -> "RuleCondition":
        crossing = self.comparison_type == "sign_transition"
        if crossing != self.operator.startswith("crosses_"):
            raise ValueError("sign_transitionはcrosses演算子、それ以外はlt/lte/gt/gteを指定してください")
        if crossing and self.threshold_value is not None:
            raise ValueError("sign_transitionに閾値は指定しません")
        if not crossing and self.threshold_value is None:
            raise ValueError("数値比較には閾値が必要です")
        return self


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scope_type: ScopeType = "investment_target"
    target_id: int | None = Field(default=None, gt=0)
    target_security_key: str | None = Field(default=None, min_length=1, max_length=100)
    mandate_id: int | None = Field(default=None, gt=0)
    theme_id: int | None = Field(default=None, gt=0)
    watchlist_status: WatchlistStatus | None = None
    definition_key: Literal["financial_metric_comparison"] = "financial_metric_comparison"
    logic_operator: LogicOperator = "all"
    conditions: list[RuleCondition] = Field(default_factory=list, min_length=0, max_length=20)
    # 0018以前のclientを壊さず、受信後にconditions 1件へ正規化する移行用入力。
    metric_key: MetricKey | None = None
    comparison_type: ComparisonType | None = None
    operator: Operator | None = None
    threshold_value: Decimal | None = Field(default=None, max_digits=20, decimal_places=4)
    on_fire_decision: DecisionType = "investigate"
    rationale: str = Field(min_length=1, max_length=2000)
    supersedes_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_condition(self) -> "RuleCreate":
        scope_values = {
            "investment_target": self.target_id,
            "mandate": self.mandate_id,
            "theme": self.theme_id,
        }
        if self.scope_type == "watchlist":
            if any(value is not None for value in scope_values.values()) or self.target_security_key is not None:
                raise ValueError("ウォッチリストscopeに他の対象IDは指定できません")
        elif self.scope_type == "investment_target":
            if (self.target_id is None) == (self.target_security_key is None):
                raise ValueError("investment_target scopeはtarget_idまたはtarget_security_keyを1つ指定してください")
            if self.mandate_id is not None or self.theme_id is not None or self.watchlist_status is not None:
                raise ValueError("適用対象は1種類だけ指定してください")
        elif scope_values[self.scope_type] is None:
            raise ValueError(f"{self.scope_type} scopeの対象IDが必要です")
        elif (
            self.target_security_key is not None
            or sum(value is not None for value in scope_values.values()) != 1
            or self.watchlist_status is not None
        ):
            raise ValueError("適用対象は1種類だけ指定してください")
        legacy = (self.metric_key, self.comparison_type, self.operator)
        if self.conditions and (any(value is not None for value in legacy) or self.threshold_value is not None):
            raise ValueError("conditionsと旧形式の条件は同時に指定できません")
        if not self.conditions:
            if any(value is None for value in legacy):
                raise ValueError("条件を1件以上指定してください")
            assert self.metric_key is not None
            assert self.comparison_type is not None
            assert self.operator is not None
            self.conditions = [
                RuleCondition(
                    metric_key=self.metric_key,
                    comparison_type=self.comparison_type,
                    operator=self.operator,
                    threshold_value=self.threshold_value,
                )
            ]
        return self


class RuleRead(BaseModel):
    rule_id: int
    rule_version_id: int
    rule_key: str
    version: int
    scope_type: ScopeType
    scope_label: str
    target_id: int | None
    target_key: str | None
    target_name: str | None
    mandate_id: int | None
    theme_id: int | None
    watchlist_status: WatchlistStatus | None
    definition_key: str
    logic_operator: LogicOperator
    conditions: list[RuleCondition]
    # 一条件Ruleを利用していたclient向けの先頭条件alias。
    metric_key: str
    comparison_type: str
    operator: str
    threshold_value: Decimal | None
    on_fire_decision: DecisionType
    rationale: str
    is_active: bool
    created_at: datetime
    actor_type: str
    actor_id: str | None
    evaluation_count: int
    response_count: int


class RuleDeleteRead(BaseModel):
    rule_id: int
    deleted_versions: int
    deleted_evaluations: int
    deleted_responses: int


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 既定は実運用。省略した呼び出しが検証用の記録を作ると、作業キューに出ない判定が
    # 黙って増える。過去再生は意図したときだけ明示する。
    mode: Literal["replay", "live"] = "live"
    as_of_date: date


class EvaluationRead(BaseModel):
    evaluation_id: int
    rule_version_id: int
    rule_id: int
    mode: Literal["replay", "live"]
    event_key: str
    as_of_date: date
    status: Literal["fired", "not_fired", "unavailable"]
    reason: str
    observed_value: Decimal | None
    reference_value: Decimal | None
    evidence: dict[str, Any]
    evidence_hash: str
    definition_version: str
    evaluated_at: datetime
    target_name: str
    target_key: str
    logic_operator: LogicOperator
    conditions: list[RuleCondition]
    metric_key: str
    comparison_type: str
    operator: str
    threshold_value: Decimal | None
    on_fire_decision: DecisionType
    response_count: int
    latest_response_disposition: ResponseDisposition | None
    next_review_on: date | None


class ResponseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    disposition: ResponseDisposition
    override_decision: DecisionType | None = None
    rationale: str = Field(min_length=1, max_length=4000)
    next_review_on: date | None = None

    @model_validator(mode="after")
    def validate_override(self) -> "ResponseCreate":
        if (self.disposition == "overridden") != (self.override_decision is not None):
            raise ValueError("上書き時だけoverride_decisionを指定してください")
        if self.disposition == "deferred" and self.next_review_on is None:
            raise ValueError("判断を保留する場合は次回確認日が必要です")
        return self


class ResponseRead(ResponseCreate):
    response_id: int
    evaluation_id: int
    actor_type: str
    actor_id: str | None
    created_at: datetime
