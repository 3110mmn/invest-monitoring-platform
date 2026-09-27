"""
InvestmentTargetRepository — アセット + theme_investment_target の CRUD
"""
from datetime import UTC, datetime
from typing import Any

from app.repositories.base import BaseRepository


class InvestmentTargetRepository(BaseRepository):

    # ---- Read ----

    def find_all(self, is_active: bool | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM investment_target"
        if is_active is not None:
            q += " WHERE is_active = ?"
            return self.execute_query(q, (is_active,))
        return self.execute_query(q + " ORDER BY target_id")

    def find_by_id(self, target_id: int) -> dict[str, Any] | None:
        return self.execute_single("SELECT * FROM investment_target WHERE target_id = ?", (target_id,))

    def get_theme_investment_targets(self, theme_id: int) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT a.*, ta.basket_weight, ta.rationale AS theme_rationale,
                   ta.is_active AS relation_is_active
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            WHERE ta.theme_id = ?
            ORDER BY ta.basket_weight DESC, a.target_name
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
                 is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, TRUE, ?, ?)
        """, "target_id", (
            data["target_key"], data["target_name"], data.get("target_type"),
            data.get("market"), data.get("currency"),
            now, now,
        ))

    def update(self, target_id: int, data: dict[str, Any]) -> bool:
        cols = ("target_name", "target_type", "market", "currency", "is_active")
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

    def upsert_theme_investment_target(self, theme_id: int, target_id: int, data: dict[str, Any]) -> bool:
        now = datetime.now(UTC)
        self.execute_write("""
            INSERT INTO theme_investment_target
                (theme_id, target_id, basket_weight, rationale, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, TRUE, ?, ?)
            ON CONFLICT(theme_id, target_id) DO UPDATE SET
                basket_weight = excluded.basket_weight,
                rationale     = excluded.rationale,
                is_active     = excluded.is_active,
                updated_at    = excluded.updated_at
        """, (
            theme_id, target_id,
            data.get("basket_weight", 1.0), data.get("rationale"),
            now, now,
        ))
        return True

    def deactivate_theme_investment_target(self, theme_id: int, target_id: int) -> bool:
        """テーマから銘柄を外す。行は消さず `is_active = FALSE` にする。

        「いつ何を、どういう理由で入れていたか」は投資判断の記録として残す。
        物理削除すると、後から仮説を検証できなくなる。再度追加すれば復帰する。
        """
        now = datetime.now(UTC)
        self.execute_write(
            "UPDATE theme_investment_target SET is_active = FALSE, updated_at = ? "
            "WHERE theme_id = ? AND target_id = ?",
            (now, theme_id, target_id),
        )
        return True

    def find_theme_investment_target(
        self, theme_id: int, target_id: int
    ) -> dict[str, Any] | None:
        """テーマ内の1銘柄を、ウェイトと採用理由つきで返す。"""
        return self.execute_single("""
            SELECT a.*, ta.basket_weight, ta.rationale AS theme_rationale,
                   ta.is_active AS relation_is_active
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            WHERE ta.theme_id = ? AND ta.target_id = ?
        """, (theme_id, target_id))
