"""Pydantic schemas for capital allocation mandate management."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewCycle = Literal["monthly", "quarterly", "semiannual", "annual", "ad_hoc", "other"]


class MandateVersionFields(BaseModel):
    purpose: str = Field(min_length=1, max_length=1000)
    allocation_weight: float | None = Field(default=None, ge=0, le=1)
    # 年率の小数。フロントは%入力を100で割って渡す。`max_drawdown` と対称に
    # 範囲を置く。範囲があると `inf` / `nan` も同時に弾ける（無制限のfloatは通る）。
    expected_return: float | None = Field(default=None, ge=-1, le=10)
    max_drawdown: float | None = Field(default=None, ge=-1, le=0)
    horizon_months: int | None = Field(default=None, gt=0)
    benchmark_target_id: int | None = None
    review_cycle: ReviewCycle | None = None
    # `review_cycle='other'` のとき必須の自由記述。周期の名前なので短い。
    review_cycle_custom: str | None = Field(default=None, max_length=200)
    next_review_at: date | None = None

    @model_validator(mode="after")
    def validate_review_cycle(self):
        if self.review_cycle == "other" and not (self.review_cycle_custom or "").strip():
            raise ValueError("review_cycle_custom is required when review_cycle is other")
        if self.review_cycle != "other" and self.review_cycle_custom is not None:
            raise ValueError("review_cycle_custom is only allowed when review_cycle is other")
        return self


class MandateCreate(MandateVersionFields):
    model_config = ConfigDict(extra="forbid")

    mandate_name: str = Field(min_length=1, max_length=100)
    status: str = Field(default="draft", pattern="^(draft|active|suspended|retired)$")
    # 分析層の銘柄識別子。実データは最長5文字、デモのkeyでも10文字程度。
    benchmark_security_key: str | None = Field(default=None, max_length=20)
    change_reason: str | None = Field(default=None, max_length=500)


class MandateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate_name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern="^(draft|active|suspended|retired)$")
    purpose: str | None = Field(default=None, min_length=1, max_length=1000)
    allocation_weight: float | None = Field(default=None, ge=0, le=1)
    expected_return: float | None = Field(default=None, ge=-1, le=10)
    max_drawdown: float | None = Field(default=None, ge=-1, le=0)
    horizon_months: int | None = Field(default=None, gt=0)
    benchmark_target_id: int | None = None
    benchmark_security_key: str | None = Field(default=None, max_length=20)
    review_cycle: ReviewCycle | None = None
    review_cycle_custom: str | None = Field(default=None, max_length=200)
    next_review_at: date | None = None
    change_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_review_cycle(self):
        if self.review_cycle == "other" and not (self.review_cycle_custom or "").strip():
            raise ValueError("review_cycle_custom is required when review_cycle is other")
        if self.review_cycle not in (None, "other") and self.review_cycle_custom is not None:
            raise ValueError("review_cycle_custom is only allowed when review_cycle is other")
        return self


class MandateAssignmentUpsert(BaseModel):
    target_id: int
    status: str = Field(default="active", pattern="^(draft|active|suspended|retired)$")
    target_weight: float | None = Field(default=None, ge=0, le=1)
    minimum_weight: float | None = Field(default=None, ge=0, le=1)
    maximum_weight: float | None = Field(default=None, ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_weight_range(self):
        if (
            self.minimum_weight is not None
            and self.maximum_weight is not None
            and self.minimum_weight > self.maximum_weight
        ):
            raise ValueError("minimum_weight cannot exceed maximum_weight")
        if self.target_weight is not None:
            if self.minimum_weight is not None and self.target_weight < self.minimum_weight:
                raise ValueError("target_weight cannot be below minimum_weight")
            if self.maximum_weight is not None and self.target_weight > self.maximum_weight:
                raise ValueError("target_weight cannot exceed maximum_weight")
        return self


class MandateSecurityAssignment(BaseModel):
    target_weight: float = Field(ge=0, le=1)
    minimum_weight: float | None = Field(default=None, ge=0, le=1)
    maximum_weight: float | None = Field(default=None, ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_weight_range(self):
        if (
            self.minimum_weight is not None
            and self.maximum_weight is not None
            and self.minimum_weight > self.maximum_weight
        ):
            raise ValueError("minimum_weight cannot exceed maximum_weight")
        if self.minimum_weight is not None and self.target_weight < self.minimum_weight:
            raise ValueError("target_weight cannot be below minimum_weight")
        if self.maximum_weight is not None and self.target_weight > self.maximum_weight:
            raise ValueError("target_weight cannot exceed maximum_weight")
        return self


class MandateAssignmentRead(BaseModel):
    assignment_id: int
    target_id: int
    target_key: str
    target_name: str
    target_type: str | None
    status: str
    target_weight: float | None
    target_amount: Decimal | None
    minimum_weight: float | None
    maximum_weight: float | None
    rationale: str | None


class MandateRead(MandateVersionFields):
    mandate_id: int
    mandate_key: str
    mandate_name: str
    status: str
    budget_amount: Decimal | None = None
    currency: str | None = None
    mandate_version_id: int
    version_no: int
    effective_from: datetime
    effective_until: datetime | None
    change_reason: str | None
    allocated_weight: float
    unallocated_weight: float | None
    created_at: datetime
    updated_at: datetime
    benchmark_target_key: str | None = None
    benchmark_target_name: str | None = None
    demo_expires_at: datetime | None = None


class CapitalBudgetUpdate(BaseModel):
    total_budget: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    change_reason: str = Field(min_length=1, max_length=500)


class CapitalBudgetRead(BaseModel):
    capital_budget_version_id: int
    version_no: int
    total_budget: Decimal
    currency: str
    effective_from: datetime
    effective_until: datetime | None
    change_reason: str


class MandateReviewItem(BaseModel):
    mandate_id: int
    mandate_name: str
    next_review_at: date


class MandateDetail(MandateRead):
    assignments: list[MandateAssignmentRead]
