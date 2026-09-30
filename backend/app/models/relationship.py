"""Pydantic スキーマ — theme_investment_target"""

from datetime import datetime

from pydantic import BaseModel

from app.models.investment_target import InvestmentTargetRead


class ThemeInvestmentTargetCreate(BaseModel):
    """テーマへの銘柄追加・更新。

    `theme_id` はURLのパスで指定するため、bodyには含めない。両方で受け取ると
    食い違った場合の扱いが曖昧になる。
    """

    target_id: int


class ThemeInvestmentTargetRead(BaseModel):
    membership_id: int
    theme_id: int
    target_id: int
    effective_from: datetime
    effective_to: datetime | None = None

    model_config = {"from_attributes": True}


class ThemeConstituentRead(InvestmentTargetRead):
    """現在テーマに所属している銘柄と、その所属開始時点。"""

    membership_id: int
    effective_from: datetime
