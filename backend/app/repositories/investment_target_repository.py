"""
InvestmentTargetRepository — アセット + theme_investment_target の CRUD
"""
from datetime import UTC, datetime
from typing import Any

from app.repositories.base import BaseRepository


class InvestmentTargetRepository(BaseRepository):

    # ---- Read ----

    def find_all(self, is_monitored: bool | None = None) -> list[dict[str, Any]]:
        q = """SELECT t.*, w.status AS watchlist_status,
                      COALESCE(w.status = 'monitoring', FALSE) AS is_monitored
               FROM investment_target t
               LEFT JOIN watchlist_entry w ON w.target_id = t.target_id"""
        if is_monitored is not None:
            q += " WHERE COALESCE(w.status = 'monitoring', FALSE) = ?"
            return self.execute_query(q + " ORDER BY t.target_id", (is_monitored,))
        return self.execute_query(q + " ORDER BY t.target_id")

    def find_by_id(self, target_id: int) -> dict[str, Any] | None:
        return self.execute_single("""SELECT t.*, w.status AS watchlist_status,
                COALESCE(w.status = 'monitoring', FALSE) AS is_monitored
                FROM investment_target t LEFT JOIN watchlist_entry w ON w.target_id = t.target_id
                WHERE t.target_id = ?""", (target_id,))

    def find_by_target_key(self, target_key: str) -> dict[str, Any] | None:
        return self.execute_single(
            """SELECT t.*, w.status AS watchlist_status,
                      COALESCE(w.status = 'monitoring', FALSE) AS is_monitored
               FROM investment_target t LEFT JOIN watchlist_entry w ON w.target_id = t.target_id
               WHERE t.target_key = ?""", (target_key,)
        )

    def find_by_target_keys(self, target_keys: list[str]) -> list[dict[str, Any]]:
        if not target_keys:
            return []
        placeholders = ", ".join("?" for _ in target_keys)
        return self.execute_query(
            f"""SELECT t.*, w.status AS watchlist_status,
                       COALESCE(w.status = 'monitoring', FALSE) AS is_monitored
                FROM investment_target t LEFT JOIN watchlist_entry w ON w.target_id = t.target_id
                WHERE t.target_key IN ({placeholders})""",
            tuple(target_keys),
        )

    def get_theme_investment_targets(self, theme_id: int) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT a.*, w.status AS watchlist_status,
                   COALESCE(w.status = 'monitoring', FALSE) AS is_monitored,
                   ta.membership_id, ta.effective_from
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            LEFT JOIN watchlist_entry w ON w.target_id = a.target_id
            WHERE ta.theme_id = ? AND ta.effective_to IS NULL
            ORDER BY a.target_name
        """, (theme_id,))

    # 価格の読み出しは `repositories/price_source.py` が持つ。所在がPostgreSQLとは
    # 限らず（分析層のParquetを読む配備がある）、この Repository は監視対象の管理に
    # 専念する。

    # ---- InvestmentTarget Write ----

    def create(self, data: dict[str, Any]) -> int:
        now = datetime.now(UTC)
        target_id = self.execute_insert("""
            INSERT INTO investment_target
                (target_key, target_name, target_type, market, currency, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, "target_id", (
            data["target_key"], data["target_name"], data.get("target_type"),
            data.get("market"), data.get("currency"),
            now, now,
        ))
        self.set_watchlist_status(target_id, "monitoring")
        return target_id

    def ensure_from_security(self, data: dict[str, Any]) -> int:
        """全銘柄マスタから選ばれた対象を登録する。Watchlistには自動追加しない。"""
        existing = self.find_by_target_key(data["target_key"])
        if existing:
            return int(existing["target_id"])
        now = datetime.now(UTC)
        row = self.execute_single("""
            INSERT INTO investment_target
                (target_key, target_name, target_type, market, currency, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_key) DO UPDATE SET target_key = excluded.target_key
            RETURNING target_id
        """, (data["target_key"], data["target_name"], data.get("target_type"),
              data.get("market"), data.get("currency"), now, now))
        if row is None:
            raise RuntimeError("Could not register investment target")
        return int(row["target_id"])

    def set_watchlist_status(self, target_id: int, status: str | None) -> None:
        if status is None:
            self.execute_write("DELETE FROM watchlist_entry WHERE target_id = ?", (target_id,))
            return
        now = datetime.now(UTC)
        self.execute_write("""
            INSERT INTO watchlist_entry (target_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET status = excluded.status,
                updated_at = excluded.updated_at
        """, (target_id, status, now, now))

    def update(self, target_id: int, data: dict[str, Any]) -> bool:
        cols = ("target_name", "target_type", "market", "currency")
        if "watchlist_status" in data:
            self.set_watchlist_status(target_id, data["watchlist_status"])
        elif "is_monitored" in data and data["is_monitored"] is not None:
            self.set_watchlist_status(target_id, "monitoring" if data["is_monitored"] else "paused")
        sets, params = [], []
        for col in cols:
            if col in data and data[col] is not None:
                sets.append(f"{col} = ?")
                params.append(data[col])
        if not sets:
            return "watchlist_status" in data or "is_monitored" in data
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
            SELECT a.*, w.status AS watchlist_status,
                   COALESCE(w.status = 'monitoring', FALSE) AS is_monitored,
                   ta.membership_id, ta.effective_from
            FROM theme_investment_target ta
            JOIN investment_target a ON ta.target_id = a.target_id
            LEFT JOIN watchlist_entry w ON w.target_id = a.target_id
            WHERE ta.theme_id = ? AND ta.target_id = ? AND ta.effective_to IS NULL
        """, (theme_id, target_id))
