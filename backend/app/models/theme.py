"""Pydantic スキーマ — theme"""

from datetime import datetime

from pydantic import BaseModel


class ThemeBase(BaseModel):
    theme_name: str
    description: str | None = None


class ThemeCreate(ThemeBase):
    # 通常の画面作成ではDBが採番する。ETL/seedは再実行時の識別用に明示可能。
    theme_key: str | None = None


class ThemeUpdate(BaseModel):
    theme_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class ThemeRead(ThemeBase):
    theme_id: int
    theme_key: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThemeDetailRead(ThemeRead):
    """テーマ詳細。"""


class ThemeSummaryRead(BaseModel):
    """テーマ一覧の集計行。構成銘柄数を添える。"""

    theme_id: int
    theme_key: str
    theme_name: str
    target_count: int
    is_active: bool

    model_config = {"from_attributes": True}
