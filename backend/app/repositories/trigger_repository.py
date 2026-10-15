"""Append-only rule versions, evidence evaluations and response history."""

import json
from datetime import date
from typing import Any
from uuid import uuid4

from app.models.trigger import ResponseCreate, RuleCreate
from app.repositories.base import BaseRepository


class TriggerRepository(BaseRepository):
    _RULE_SELECT = """
        SELECT r.rule_id, r.rule_key, r.is_active,
               v.rule_version_id, v.version, v.target_id, v.definition_key,
               v.metric_key, v.comparison_type, v.operator, v.threshold_value,
               v.rationale, v.actor_type, v.actor_id, v.created_at,
               t.target_key, t.target_name
        FROM trigger_rule r
        JOIN trigger_rule_version v ON v.rule_id = r.rule_id
        JOIN investment_target t ON t.target_id = v.target_id
    """

    def rules(self) -> list[dict[str, Any]]:
        return self.execute_query(
            self._RULE_SELECT
            + """
            WHERE v.version = (SELECT MAX(v2.version) FROM trigger_rule_version v2 WHERE v2.rule_id = r.rule_id)
            ORDER BY r.rule_id DESC
        """
        )

    def rule(self, rule_id: int, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " FOR UPDATE OF r" if lock else ""
        return self.execute_single(
            self._RULE_SELECT
            + """
            WHERE r.rule_id = ?
              AND v.version = (SELECT MAX(v2.version) FROM trigger_rule_version v2 WHERE v2.rule_id = r.rule_id)
        """
            + suffix,
            (rule_id,),
        )

    def create_rule(self, body: RuleCreate) -> int:
        if body.supersedes_id is None:
            rule_id = self.execute_insert("INSERT INTO trigger_rule (rule_key) VALUES (?)", "rule_id", (str(uuid4()),))
            version = 1
        else:
            previous = self.rule(body.supersedes_id, lock=True)
            if not previous or not previous["is_active"]:
                raise ValueError("改訂元の有効なルールがありません")
            if previous["target_id"] != body.target_id:
                raise ValueError("改訂時に対象は変更できません")
            rule_id, version = body.supersedes_id, previous["version"] + 1
        self.execute_insert(
            """
            INSERT INTO trigger_rule_version
                (rule_id, version, target_id, definition_key, metric_key, comparison_type,
                 operator, threshold_value, rationale, actor_type, actor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'human', 'owner')
        """,
            "rule_version_id",
            (
                rule_id,
                version,
                body.target_id,
                body.definition_key,
                body.metric_key,
                body.comparison_type,
                body.operator,
                body.threshold_value,
                body.rationale,
            ),
        )
        return rule_id

    def evaluations(self, rule_id: int | None = None) -> list[dict[str, Any]]:
        return self.execute_query(
            """
            SELECT e.*, v.rule_id, v.metric_key, v.comparison_type, v.operator, v.threshold_value,
                   t.target_key, t.target_name
            FROM trigger_evaluation e
            JOIN trigger_rule_version v USING (rule_version_id)
            JOIN investment_target t USING (target_id)
        """
            + (" WHERE v.rule_id = ?" if rule_id is not None else "")
            + " ORDER BY e.evaluation_id DESC LIMIT 200",
            (rule_id,) if rule_id is not None else (),
        )

    def save_evaluation(self, rule_version_id: int, mode: str, as_of: date, result: dict[str, Any]) -> bool:
        return (
            self.execute_write(
                """
            INSERT INTO trigger_evaluation
                (rule_version_id, mode, event_key, as_of_date, status, reason, observed_value,
                 reference_value, evidence, evidence_hash, definition_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?)
            ON CONFLICT (rule_version_id, mode, event_key) DO NOTHING
        """,
                (
                    rule_version_id,
                    mode,
                    result["event_key"],
                    as_of,
                    result["status"],
                    result["reason"],
                    result["observed_value"],
                    result["reference_value"],
                    json.dumps(result["evidence"], ensure_ascii=False),
                    result["evidence_hash"],
                    result["definition_version"],
                ),
            )
            > 0
        )

    def responses(self, evaluation_id: int) -> list[dict[str, Any]]:
        return self.execute_query(
            "SELECT * FROM trigger_response WHERE evaluation_id = ? ORDER BY response_id", (evaluation_id,)
        )

    def respond(self, evaluation_id: int, body: ResponseCreate) -> int:
        return self.execute_insert(
            """
            INSERT INTO trigger_response
                (evaluation_id, response_type, rationale, next_review_on, actor_type, actor_id)
            VALUES (?, ?, ?, ?, 'human', 'owner')
        """,
            "response_id",
            (evaluation_id, body.response_type, body.rationale, body.next_review_on),
        )
