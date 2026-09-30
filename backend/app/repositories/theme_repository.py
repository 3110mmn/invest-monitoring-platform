"""
ThemeRepository — テーマ + ストラテジーの CRUD
"""
from datetime import UTC, datetime
from typing import Any

from app.repositories.base import BaseRepository


class ThemeRepository(BaseRepository):

    # ---- Read ----

    def find_all(self, is_active: bool | None = None) -> list[dict[str, Any]]:
        q = """
            SELECT theme_id, theme_key, theme_name, strategy_id,
                   description, is_active, created_at, updated_at
            FROM theme
        """
        if is_active is not None:
            q += " WHERE is_active = ?"
            return self.execute_query(q, (is_active,))
        return self.execute_query(q)

    def find_by_id(self, theme_id: int) -> dict[str, Any] | None:
        return self.execute_single(
            "SELECT * FROM theme WHERE theme_id = ?", (theme_id,)
        )

    def find_detail_by_id(self, theme_id: int) -> dict[str, Any] | None:
        """テーマ1件を、所属strategyのキーと表示名つきで返す。

        `strategy_id` だけを返すと、呼び出し側が strategy 一覧を別途取得して
        突き合わせる必要が生じるため、ここで解決する。
        """
        return self.execute_single("""
            SELECT t.*, s.strategy_key, s.strategy_name
            FROM theme t
            JOIN strategy s ON s.strategy_id = t.strategy_id
            WHERE t.theme_id = ?
        """, (theme_id,))

    def find_by_key(self, theme_key: str) -> dict[str, Any] | None:
        return self.execute_single(
            "SELECT * FROM theme WHERE theme_key = ?", (theme_key,)
        )

    def get_theme_summary(self) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT
                t.theme_id, t.theme_key, t.theme_name, t.strategy_id,
                s.strategy_key, s.strategy_name,
                COUNT(DISTINCT ta.target_id) AS target_count,
                t.is_active
            FROM theme t
            JOIN strategy s ON s.strategy_id = t.strategy_id
            LEFT JOIN theme_investment_target ta
              ON t.theme_id = ta.theme_id AND ta.effective_to IS NULL
            WHERE t.is_active = TRUE
            GROUP BY t.theme_id, s.strategy_id, s.strategy_key, s.strategy_name
            ORDER BY s.strategy_key, t.theme_name
        """)

    # ---- Write ----

    def create(self, data: dict[str, Any]) -> int:
        now = datetime.now(UTC)
        return self.execute_insert("""
            INSERT INTO theme (theme_key, theme_name, strategy_id,
                               description, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, TRUE, ?, ?)
        """, "theme_id", (
            data["theme_key"], data["theme_name"], data["strategy_id"],
            data.get("description"), now, now,
        ))

    def update(self, theme_id: int, data: dict[str, Any]) -> bool:
        sets, params = [], []
        for col in ("theme_name", "strategy_id", "description", "is_active"):
            if col in data and data[col] is not None:
                sets.append(f"{col} = ?")
                params.append(data[col])
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(datetime.now(UTC))
        params.append(theme_id)
        self.execute_write(f"UPDATE theme SET {', '.join(sets)} WHERE theme_id = ?", tuple(params))
        return True

    def soft_delete(self, theme_id: int) -> bool:
        self.execute_write(
            "UPDATE theme SET is_active = FALSE, updated_at = ? WHERE theme_id = ?",
            (datetime.now(UTC), theme_id),
        )
        return True

    # ---- Strategy ----

    def find_all_strategies(self) -> list[dict[str, Any]]:
        return self.execute_query("SELECT * FROM strategy ORDER BY strategy_id")

    def create_strategy(self, data: dict[str, Any]) -> int:
        now = datetime.now(UTC)
        return self.execute_insert("""
            INSERT INTO strategy (strategy_key, strategy_name, description, is_active, created_at, updated_at)
            VALUES (?, ?, ?, TRUE, ?, ?)
        """, "strategy_id", (data["strategy_key"], data["strategy_name"], data.get("description"), now, now))
