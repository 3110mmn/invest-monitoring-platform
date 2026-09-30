"""
InvestmentTargetRepository — アセット + theme_investment_target の CRUD
"""
from datetime import UTC, datetime
from typing import Any

from app.repositories.base import BaseRepository


class InvestmentTargetRepository(BaseRepository):

    # ---- Read ----

    def find_all(self, is_monitored: bool | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM investment_target"
        if is_monitored is not None:
            q += " WHERE is_monitored = ?"
            return self.execute_query(q, (is_monitored,))
        return self.execute_query(q + " ORDER BY target_id")

    def find_by_id(self, target_id: int) -> dict[str, Any] | None:
        return self.execute_single("SELECT * FROM investment_target WHERE target_id = ?", (target_id,))

    def get_theme_investment_targets(self, theme_id: int) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT a.*, ta.membership_id, ta.effective_from
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            WHERE ta.theme_id = ? AND ta.effective_to IS NULL
            ORDER BY a.target_name
        """, (theme_id,))

    # 価格の読み出しは `repositories/price_source.py` が持つ。所在がPostgreSQLとは
    # 限らず（分析層のParquetを読む配備がある）、この Repository は監視対象の管理に
    # 専念する。

    # ---- InvestmentTarget Write ----

    def create(self, data: dict[str, Any]) -> int:
        now = datetime.now(UTC)
        return self.execute_insert("""
            INSERT INTO investment_target
                (target_key, target_name, target_type, market, currency,
                 is_monitored, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, TRUE, ?, ?)
        """, "target_id", (
            data["target_key"], data["target_name"], data.get("target_type"),
            data.get("market"), data.get("currency"),
            now, now,
        ))

    def update(self, target_id: int, data: dict[str, Any]) -> bool:
        cols = ("target_name", "target_type", "market", "currency", "is_monitored")
        sets, params = [], []
        for col in cols:
            if col in data and data[col] is not None:
                sets.append(f"{col} = ?")
                params.append(data[col])
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.extend([datetime.now(UTC), target_id])
        self.execute_write(
            f"UPDATE investment_target SET {', '.join(sets)} WHERE target_id = ?", tuple(params)
        )
        return True

    # ---- theme_investment_target Write ----

    def add_theme_investment_target(self, theme_id: int, target_id: int) -> bool:
        """現在の所属が無いときだけ、新しい有効期間を開始する。"""
        now = datetime.now(UTC)
        self.execute_write("""
            INSERT INTO theme_investment_target
                (theme_id, target_id, effective_from, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(theme_id, target_id) WHERE effective_to IS NULL DO NOTHING
        """, (theme_id, target_id, now, now, now))
        return True

    def deactivate_theme_investment_target(self, theme_id: int, target_id: int) -> bool:
        """現在の所属期間を閉じる。過去の所属行は上書きしない。"""
        now = datetime.now(UTC)
        self.execute_write(
            "UPDATE theme_investment_target SET effective_to = ?, updated_at = ? "
            "WHERE theme_id = ? AND target_id = ? AND effective_to IS NULL",
            (now, now, theme_id, target_id),
        )
        return True

    def find_theme_investment_target(
        self, theme_id: int, target_id: int
    ) -> dict[str, Any] | None:
        """テーマ内で現在有効な1銘柄を返す。"""
        return self.execute_single("""
            SELECT a.*, ta.membership_id, ta.effective_from
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            WHERE ta.theme_id = ? AND ta.target_id = ? AND ta.effective_to IS NULL
        """, (theme_id, target_id))
