"""Repository for versioned capital allocation mandates."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.config import settings
from app.repositories.base import BaseRepository


class MandateRepository(BaseRepository):
    _CURRENT_SELECT = """
        SELECT m.mandate_id, m.mandate_key, m.mandate_name, m.status,
               m.demo_expires_at,
               m.created_at, m.updated_at,
               v.mandate_version_id, v.version_no, v.purpose, v.allocation_weight,
               CASE WHEN cb.total_budget IS NOT NULL AND v.allocation_weight IS NOT NULL
                    THEN (cb.total_budget * v.allocation_weight)::NUMERIC(20, 2)
                    ELSE NULL END AS budget_amount,
               CASE WHEN v.allocation_weight IS NOT NULL
                    THEN cb.currency ELSE NULL END AS currency,
               v.expected_return, v.max_drawdown,
               v.horizon_months, v.benchmark_target_id, v.review_cycle,
               v.review_cycle_custom,
               v.next_review_at, v.effective_from, v.effective_until,
               v.change_reason, bt.target_key AS benchmark_target_key,
               bt.target_name AS benchmark_target_name,
               COALESCE(SUM(a.target_weight), 0)::DOUBLE PRECISION AS allocated_weight
        FROM capital_allocation_mandate m
        JOIN mandate_version v
          ON v.mandate_id = m.mandate_id AND v.effective_until IS NULL
        LEFT JOIN investment_target bt ON bt.target_id = v.benchmark_target_id
        LEFT JOIN capital_budget_version cb ON cb.effective_until IS NULL
        LEFT JOIN mandate_target_assignment a
          ON a.mandate_version_id = v.mandate_version_id
         AND a.effective_until IS NULL
         AND a.status IN ('draft', 'active')
        WHERE (m.demo_expires_at IS NULL OR m.demo_expires_at > CURRENT_TIMESTAMP)
    """

    @staticmethod
    def _with_unallocated(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        total = float(row["allocated_weight"])
        row["unallocated_weight"] = max(0.0, 1.0 - total) if total else 1.0
        return row

    def find_all(self) -> list[dict[str, Any]]:
        rows = self.execute_query(
            self._CURRENT_SELECT
            + " GROUP BY m.mandate_id, v.mandate_version_id, bt.target_id, "
              "cb.capital_budget_version_id ORDER BY m.mandate_name"
        )
        for row in rows:
            self._with_unallocated(row)
        return rows

    def find_current(self, mandate_id: int) -> dict[str, Any] | None:
        row = self.execute_single(
            self._CURRENT_SELECT
            + " AND m.mandate_id = ? GROUP BY m.mandate_id, v.mandate_version_id, "
              "bt.target_id, cb.capital_budget_version_id",
            (mandate_id,),
        )
        return self._with_unallocated(row)

    def list_assignments(self, mandate_version_id: int) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT a.assignment_id, a.target_id, t.target_key, t.target_name,
                   t.target_type, a.status, a.target_weight,
                   CASE
                       WHEN a.target_weight IS NULL THEN NULL
                       WHEN cb.total_budget IS NOT NULL AND v.allocation_weight IS NOT NULL
                       THEN (cb.total_budget * v.allocation_weight * a.target_weight)::NUMERIC(20, 2)
                       ELSE NULL
                   END AS target_amount,
                   a.minimum_weight, a.maximum_weight, a.rationale
            FROM mandate_target_assignment a
            JOIN mandate_version v ON v.mandate_version_id = a.mandate_version_id
            JOIN investment_target t ON t.target_id = a.target_id
            LEFT JOIN capital_budget_version cb ON cb.effective_until IS NULL
            WHERE a.mandate_version_id = ? AND a.effective_until IS NULL
            ORDER BY a.target_weight DESC NULLS LAST, t.target_name
        """, (mandate_version_id,))

    def create(self, data: dict[str, Any], *, demo_expires_at: datetime | None = None) -> int:
        now = datetime.now(UTC)
        self._lock_allocation()
        if demo_expires_at is not None:
            # Serialize quota check + insert so concurrent public requests cannot overrun it.
            self.execute_single("SELECT pg_advisory_xact_lock(917314)")
            row = self.execute_single("""
                SELECT COUNT(*) AS active_count
                FROM capital_allocation_mandate
                WHERE demo_expires_at > CURRENT_TIMESTAMP
            """)
            if row and int(row["active_count"]) >= settings.public_demo_max_active_mandates:
                raise ValueError("Public demo mandate limit reached; try again after cleanup")
        self._validate_mandate_allocation(
            data.get("allocation_weight"), data["status"]
        )
        mandate_id = self.execute_insert("""
            INSERT INTO capital_allocation_mandate (
                mandate_name, status, demo_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
        """, "mandate_id", (
            data["mandate_name"], data["status"], demo_expires_at, now, now,
        ))
        self.execute_insert("""
            INSERT INTO mandate_version (
                mandate_id, version_no, purpose, allocation_weight,
                expected_return, max_drawdown, horizon_months, benchmark_target_id,
                review_cycle, review_cycle_custom, next_review_at,
                effective_from, change_reason, created_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, "mandate_version_id", (
            mandate_id, data["purpose"], data.get("allocation_weight"),
            data.get("expected_return"),
            data.get("max_drawdown"), data.get("horizon_months"),
            data.get("benchmark_target_id"), data.get("review_cycle"),
            data.get("review_cycle_custom"), data.get("next_review_at"),
            now, data.get("change_reason"), now,
        ))
        return mandate_id

    def is_active_public_demo_mandate(self, mandate_id: int) -> bool:
        return self.execute_single("""
            SELECT 1
            FROM capital_allocation_mandate
            WHERE mandate_id = ? AND demo_expires_at > CURRENT_TIMESTAMP
        """, (mandate_id,)) is not None

    def count_current_assignments(self, mandate_version_id: int) -> int:
        row = self.execute_single("""
            SELECT COUNT(*) AS assignment_count
            FROM mandate_target_assignment
            WHERE mandate_version_id = ? AND effective_until IS NULL
        """, (mandate_version_id,))
        return int(row["assignment_count"]) if row else 0

    def has_current_assignment(self, mandate_version_id: int, target_id: int) -> bool:
        return self.execute_single("""
            SELECT 1 FROM mandate_target_assignment
            WHERE mandate_version_id = ? AND target_id = ? AND effective_until IS NULL
        """, (mandate_version_id, target_id)) is not None

    def lock_public_demo_assignments(self) -> None:
        self.execute_single("SELECT pg_advisory_xact_lock(917315)")

    def create_version(self, mandate_id: int, changes: dict[str, Any]) -> bool:
        self._lock_allocation()
        current = self.find_current(mandate_id)
        if current is None:
            return False
        now = datetime.now(UTC)
        version_fields = (
            "purpose", "allocation_weight", "expected_return", "max_drawdown",
            "horizon_months", "benchmark_target_id", "review_cycle",
            "review_cycle_custom", "next_review_at",
        )
        values = {field: changes.get(field, current[field]) for field in version_fields}
        next_status = changes.get("status", current["status"])
        self._validate_mandate_allocation(
            values["allocation_weight"], next_status, exclude_mandate_id=mandate_id
        )
        self.execute_write("""
            UPDATE capital_allocation_mandate
            SET mandate_name = ?, status = ?, updated_at = ? WHERE mandate_id = ?
        """, (
            changes.get("mandate_name", current["mandate_name"]),
            changes.get("status", current["status"]), now, mandate_id,
        ))
        self.execute_write(
            "UPDATE mandate_version SET effective_until = ? WHERE mandate_version_id = ?",
            (now, current["mandate_version_id"]),
        )
        new_version_id = self.execute_insert("""
            INSERT INTO mandate_version (
                mandate_id, version_no, purpose, allocation_weight,
                expected_return, max_drawdown, horizon_months, benchmark_target_id,
                review_cycle, review_cycle_custom, next_review_at,
                effective_from, change_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, "mandate_version_id", (
            mandate_id, current["version_no"] + 1, values["purpose"],
            values["allocation_weight"],
            values["expected_return"], values["max_drawdown"], values["horizon_months"],
            values["benchmark_target_id"], values["review_cycle"],
            values["review_cycle_custom"], values["next_review_at"], now,
            changes["change_reason"], now,
        ))
        self.execute_write("""
            INSERT INTO mandate_target_assignment (
                mandate_version_id, target_id, status, target_weight,
                minimum_weight, maximum_weight, effective_from, effective_until,
                rationale, created_at, updated_at
            )
            SELECT ?, target_id, status, target_weight, minimum_weight, maximum_weight,
                   ?, NULL, rationale, ?, ?
            FROM mandate_target_assignment
            WHERE mandate_version_id = ? AND effective_until IS NULL
        """, (new_version_id, now, now, now, current["mandate_version_id"]))
        self.execute_write("""
            UPDATE mandate_target_assignment
            SET effective_until = ?, updated_at = ?
            WHERE mandate_version_id = ? AND effective_until IS NULL
        """, (now, now, current["mandate_version_id"]))
        return True

    def _lock_allocation(self) -> None:
        # Serialize API writes so the <=100% check sees the previous committed change.
        self.execute_single("SELECT pg_advisory_xact_lock(42007)")

    def _validate_mandate_allocation(
        self,
        weight: float | None,
        status: str,
        *,
        exclude_mandate_id: int | None = None,
    ) -> None:
        if weight is None or status not in {"draft", "active"}:
            return
        params: tuple[Any, ...] = ()
        exclusion = ""
        if exclude_mandate_id is not None:
            exclusion = " AND m.mandate_id <> ?"
            params = (exclude_mandate_id,)
        row = self.execute_single("""
            SELECT COALESCE(SUM(v.allocation_weight), 0) AS total
            FROM capital_allocation_mandate m
            JOIN mandate_version v
              ON v.mandate_id = m.mandate_id AND v.effective_until IS NULL
            WHERE m.status IN ('draft', 'active')
              AND (m.demo_expires_at IS NULL OR m.demo_expires_at > CURRENT_TIMESTAMP)
        """ + exclusion, params)
        total = Decimal(str(row["total"] if row else 0)) + Decimal(str(weight))
        if total > Decimal("1.000000001"):
            raise ValueError("Total mandate allocation weight cannot exceed 1.0")

    def delete(self, mandate_id: int) -> bool:
        return self.execute_write(
            "DELETE FROM capital_allocation_mandate WHERE mandate_id = ?", (mandate_id,)
        ) > 0

    def get_capital_budget(self) -> dict[str, Any] | None:
        return self.execute_single("""
            SELECT capital_budget_version_id, version_no, total_budget, currency,
                   effective_from, effective_until, change_reason
            FROM capital_budget_version
            WHERE effective_until IS NULL
        """)

    def list_review_items(self) -> list[dict[str, Any]]:
        return self.execute_query("""
            SELECT m.mandate_id, m.mandate_name, v.next_review_at
            FROM capital_allocation_mandate m
            JOIN mandate_version v
              ON v.mandate_id = m.mandate_id AND v.effective_until IS NULL
            WHERE m.status = 'active' AND v.next_review_at IS NOT NULL
              AND (m.demo_expires_at IS NULL OR m.demo_expires_at > CURRENT_TIMESTAMP)
            ORDER BY v.next_review_at, m.mandate_name
        """)

    def set_capital_budget(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.get_capital_budget()
        now = datetime.now(UTC)
        if current is not None:
            self.execute_write(
                "UPDATE capital_budget_version SET effective_until = ? WHERE capital_budget_version_id = ?",
                (now, current["capital_budget_version_id"]),
            )
        version_no = int(current["version_no"]) + 1 if current else 1
        self.execute_insert("""
            INSERT INTO capital_budget_version (
                version_no, total_budget, currency, effective_from, change_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, "capital_budget_version_id", (
            version_no, data["total_budget"], str(data["currency"]).upper(),
            now, data["change_reason"], now,
        ))
        result = self.get_capital_budget()
        if result is None:
            raise RuntimeError("Capital budget version was not created")
        return result

    def target_exists(self, target_id: int) -> bool:
        return self.execute_single(
            "SELECT target_id FROM investment_target WHERE target_id = ?", (target_id,)
        ) is not None

    def upsert_assignment(self, mandate_version_id: int, data: dict[str, Any]) -> None:
        self._lock_allocation()
        current = self.execute_single("""
            SELECT assignment_id, status, target_weight, minimum_weight,
                   maximum_weight, rationale
            FROM mandate_target_assignment
            WHERE mandate_version_id = ? AND target_id = ? AND effective_until IS NULL
        """, (mandate_version_id, data["target_id"]))
        values = (
            data["status"], data.get("target_weight"), data.get("minimum_weight"),
            data.get("maximum_weight"), data.get("rationale"),
        )
        if current is not None and values == (
            current["status"], current["target_weight"], current["minimum_weight"],
            current["maximum_weight"], current["rationale"],
        ):
            return
        current_total = self.execute_single("""
            SELECT COALESCE(SUM(target_weight), 0) AS total
            FROM mandate_target_assignment
            WHERE mandate_version_id = ? AND target_id <> ?
              AND effective_until IS NULL AND status IN ('draft', 'active')
        """, (mandate_version_id, data["target_id"]))
        weight = data.get("target_weight")
        total = Decimal(str(current_total["total"] if current_total else 0))
        if weight is not None and data["status"] in {"draft", "active"}:
            total += Decimal(str(weight))
        if total > Decimal("1.000000001"):
            raise ValueError("Total target weight cannot exceed 1.0")
        now = datetime.now(UTC)
        if current is not None:
            self.execute_write("""
                UPDATE mandate_target_assignment
                SET effective_until = ?, updated_at = ?
                WHERE assignment_id = ? AND effective_until IS NULL
            """, (now, now, current["assignment_id"]))
        self.execute_write("""
            INSERT INTO mandate_target_assignment (
                mandate_version_id, target_id, status, target_weight,
                minimum_weight, maximum_weight, effective_from, rationale,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mandate_version_id, data["target_id"], data["status"], weight,
            data.get("minimum_weight"), data.get("maximum_weight"),
            now, data.get("rationale"), now, now,
        ))

    def delete_assignment(self, mandate_version_id: int, target_id: int) -> bool:
        now = datetime.now(UTC)
        return self.execute_write("""
            UPDATE mandate_target_assignment
            SET effective_until = ?, updated_at = ?
            WHERE mandate_version_id = ? AND target_id = ? AND effective_until IS NULL
        """, (now, now, mandate_version_id, target_id)) > 0
