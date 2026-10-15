"""Contracts shared by all operational trigger definitions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MetricKey = Literal["revenue", "operating_income", "net_income"]
ComparisonType = Literal["forecast_variance", "yoy", "absolute", "sign_transition"]
Operator = Literal["lte", "gte", "crosses_below", "crosses_above"]


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_id: int = Field(gt=0)
    definition_key: Literal["financial_metric_comparison"] = "financial_metric_comparison"
    metric_key: MetricKey
    comparison_type: ComparisonType
    operator: Operator
    threshold_value: Decimal | None = Field(default=None, max_digits=20, decimal_places=4)
    rationale: str = Field(min_length=1, max_length=2000)
    supersedes_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_condition(self) -> "RuleCreate":
        crossing = self.comparison_type == "sign_transition"
        if crossing != self.operator.startswith("crosses_"):
            raise ValueError("sign_transitionはcrosses演算子、それ以外はlte/gteを指定してください")
        if crossing and self.threshold_value is not None:
            raise ValueError("sign_transitionに閾値は指定しません")
        if not crossing and self.threshold_value is None:
            raise ValueError("数値比較には閾値が必要です")
        return self


class RuleRead(BaseModel):
    rule_id: int
    rule_version_id: int
    rule_key: str
    version: int
    target_id: int
    target_key: str
    target_name: str
    definition_key: str
    metric_key: str
    comparison_type: str
    operator: str
    threshold_value: Decimal | None
    rationale: str
    is_active: bool
    created_at: datetime
    actor_type: str
    actor_id: str | None


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["replay", "live"] = "replay"
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
    metric_key: str
    comparison_type: str
    operator: str
    threshold_value: Decimal | None


class ResponseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    response_type: Literal["continue", "investigate", "review_allocation", "hold"]
    rationale: str = Field(min_length=1, max_length=4000)
    next_review_on: date | None = None


class ResponseRead(ResponseCreate):
    response_id: int
    evaluation_id: int
    actor_type: str
    actor_id: str | None
    created_at: datetime
