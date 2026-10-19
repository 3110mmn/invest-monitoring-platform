"""Theme membership snapshot import history."""

from datetime import datetime
from typing import Any

from app.repositories.base import BaseRepository


class ThemeMembershipImportRepository(BaseRepository):
    def find_duplicate(
        self,
        *,
        theme_id: int,
        snapshot_at: datetime,
        import_mode: str,
        artifact_hash: str,
    ) -> dict[str, Any] | None:
        return self.execute_single("""
            SELECT * FROM theme_membership_import
            WHERE theme_id = ? AND snapshot_at = ?
              AND import_mode = ? AND artifact_hash = ?
        """, (theme_id, snapshot_at, import_mode, artifact_hash))

    def create(self, data: dict[str, Any]) -> int:
        return self.execute_insert("""
            INSERT INTO theme_membership_import (
                theme_id, snapshot_at, import_mode,
                artifact_name, artifact_ref, artifact_hash,
                rationale, actor, input_count,
                added_count, removed_count, unchanged_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, "import_id", (
            data["theme_id"], data["snapshot_at"], data["import_mode"],
            data["artifact_name"], data["artifact_ref"], data["artifact_hash"],
            data.get("rationale"), data.get("actor", "owner"), data["input_count"],
            data["added_count"], data["removed_count"], data["unchanged_count"],
        ))
