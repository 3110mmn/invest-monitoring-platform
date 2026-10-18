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
               v.rule_version_id, v.version, v.definition_key,
               v.logic_operator,
               c.metric_key, c.comparison_type, c.operator, c.threshold_value,
               (SELECT jsonb_agg(jsonb_build_object(
                    'metric_key', condition.metric_key,
                    'comparison_type', condition.comparison_type,
                    'operator', condition.operator,
                    'threshold_value', condition.threshold_value
                ) ORDER BY condition.position)
                FROM trigger_rule_condition condition
                WHERE condition.rule_version_id = v.rule_version_id) AS conditions,
               v.on_fire_decision,
               v.rationale, v.actor_type, v.actor_id, v.created_at,
               (SELECT COUNT(*) FROM trigger_evaluation evaluation
                JOIN trigger_rule_version counted_version USING (rule_version_id)
                WHERE counted_version.rule_id = r.rule_id) AS evaluation_count,
               (SELECT COUNT(*) FROM trigger_response response
                JOIN trigger_evaluation evaluation USING (evaluation_id)
                JOIN trigger_rule_version counted_version USING (rule_version_id)
                WHERE counted_version.rule_id = r.rule_id) AS response_count,
               a.scope_type, a.target_id, a.mandate_id, a.theme_id, a.watchlist_status,
               t.target_key, t.target_name,
               CASE a.scope_type
                   WHEN 'investment_target' THEN t.target_name
                   WHEN 'mandate' THEN m.mandate_name
                   WHEN 'theme' THEN th.theme_name
                   WHEN 'watchlist' THEN
                       CASE WHEN a.watchlist_status IS NULL THEN 'ウォッチリスト全体'
                            ELSE 'ウォッチリスト: ' || a.watchlist_status END
               END AS scope_label
        FROM trigger_rule r
        JOIN trigger_rule_version v ON v.rule_id = r.rule_id
        JOIN trigger_rule_condition c ON c.rule_version_id = v.rule_version_id AND c.position = 1
        JOIN trigger_rule_assignment a ON a.rule_version_id = v.rule_version_id
        LEFT JOIN investment_target t ON t.target_id = a.target_id
        LEFT JOIN capital_allocation_mandate m ON m.mandate_id = a.mandate_id
        LEFT JOIN theme th ON th.theme_id = a.theme_id
    """

    def rules(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """最新versionのRuleを返す。既定では停止済みを除く。"""
        condition = "" if include_inactive else " AND r.is_active"
        return self.execute_query(
            self._RULE_SELECT
            + f"""
            WHERE v.version = (SELECT MAX(v2.version) FROM trigger_rule_version v2 WHERE v2.rule_id = r.rule_id)
              {condition}
            ORDER BY r.is_active DESC, r.rule_id DESC
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
            if any(
                previous[key] != getattr(body, key)
                for key in ("scope_type", "target_id", "mandate_id", "theme_id", "watchlist_status")
            ):
                raise ValueError("改訂時に対象は変更できません")
            rule_id, version = body.supersedes_id, previous["version"] + 1
        rule_version_id = self.execute_insert(
            """
            INSERT INTO trigger_rule_version
                (rule_id, version, definition_key, logic_operator,
                 on_fire_decision, rationale, actor_type, actor_id)
            VALUES (?, ?, ?, ?, ?, ?, 'human', 'owner')
        """,
            "rule_version_id",
            (
                rule_id,
                version,
                body.definition_key,
                body.logic_operator,
                body.on_fire_decision,
                body.rationale,
            ),
        )
        for position, condition in enumerate(body.conditions, start=1):
            self.execute_insert(
                """
                INSERT INTO trigger_rule_condition
                    (rule_version_id, position, metric_key, comparison_type, operator, threshold_value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "condition_id",
                (
                    rule_version_id,
                    position,
                    condition.metric_key,
                    condition.comparison_type,
                    condition.operator,
                    condition.threshold_value,
                ),
            )
        self.execute_insert(
            """
            INSERT INTO trigger_rule_assignment
                (rule_version_id, scope_type, target_id, mandate_id, theme_id, watchlist_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            "assignment_id",
            (
                rule_version_id,
                body.scope_type,
                body.target_id,
                body.mandate_id,
                body.theme_id,
                body.watchlist_status,
            ),
        )
        return rule_id

    def scope_exists(self, body: RuleCreate) -> bool:
        if body.scope_type == "investment_target":
            return (
                self.execute_single("SELECT target_id FROM investment_target WHERE target_id = ?", (body.target_id,))
                is not None
            )
        if body.scope_type == "mandate":
            return (
                self.execute_single(
                    "SELECT mandate_id FROM capital_allocation_mandate WHERE mandate_id = ?", (body.mandate_id,)
                )
                is not None
            )
        if body.scope_type == "theme":
            return self.execute_single("SELECT theme_id FROM theme WHERE theme_id = ?", (body.theme_id,)) is not None
        return True

    def delete_rule(self, rule_id: int) -> dict[str, int]:
        """停止済みRuleと、そのRuleだけに属する履歴を集約単位で完全削除する。"""
        rule = self.execute_single(
            "SELECT rule_id, is_active FROM trigger_rule WHERE rule_id = ? FOR UPDATE",
            (rule_id,),
        )
        if rule is None:
            raise LookupError("ルールがありません")
        if rule["is_active"]:
            raise ValueError("稼働中のルールは削除できません。先に停止してください")
        versions = self.execute_single(
            "SELECT COUNT(*) AS count FROM trigger_rule_version WHERE rule_id = ?", (rule_id,)
        )
        evaluations = self.execute_single(
            """
            SELECT COUNT(*) AS count
            FROM trigger_evaluation evaluation
            JOIN trigger_rule_version version USING (rule_version_id)
            WHERE version.rule_id = ?
            """,
            (rule_id,),
        )
        responses = self.execute_single(
            """
            SELECT COUNT(*) AS count
            FROM trigger_response response
            JOIN trigger_evaluation evaluation USING (evaluation_id)
            JOIN trigger_rule_version version USING (rule_version_id)
            WHERE version.rule_id = ?
            """,
            (rule_id,),
        )
        self.execute_write(
            """
            DELETE FROM trigger_response
            WHERE evaluation_id IN (
                SELECT evaluation.evaluation_id
                FROM trigger_evaluation evaluation
                JOIN trigger_rule_version version USING (rule_version_id)
                WHERE version.rule_id = ?
            )
            """,
            (rule_id,),
        )
        self.execute_write(
            """
            DELETE FROM trigger_evaluation
            WHERE rule_version_id IN (
                SELECT rule_version_id FROM trigger_rule_version WHERE rule_id = ?
            )
            """,
            (rule_id,),
        )
        self.execute_write(
            """
            DELETE FROM trigger_rule_assignment
            WHERE rule_version_id IN (
                SELECT rule_version_id FROM trigger_rule_version WHERE rule_id = ?
            )
            """,
            (rule_id,),
        )
        self.execute_write(
            """
            DELETE FROM trigger_rule_condition
            WHERE rule_version_id IN (
                SELECT rule_version_id FROM trigger_rule_version WHERE rule_id = ?
            )
            """,
            (rule_id,),
        )
        self.execute_write("DELETE FROM trigger_rule_version WHERE rule_id = ?", (rule_id,))
        self.execute_write("DELETE FROM trigger_rule WHERE rule_id = ?", (rule_id,))
        return {
            "rule_id": rule_id,
            "deleted_versions": int(versions["count"] if versions else 0),
            "deleted_evaluations": int(evaluations["count"] if evaluations else 0),
            "deleted_responses": int(responses["count"] if responses else 0),
        }

    def scope_targets(self, rule: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
        if rule["scope_type"] == "investment_target":
            return self.execute_query(
                "SELECT target_id, target_key, target_name FROM investment_target WHERE target_id = ?",
                (rule["target_id"],),
            )
        if rule["scope_type"] == "theme":
            return self.execute_query(
                """
                SELECT DISTINCT t.target_id, t.target_key, t.target_name
                FROM theme_investment_target membership
                JOIN investment_target t USING (target_id)
                WHERE membership.theme_id = ?
                  AND membership.effective_from::date <= ?
                  AND (membership.effective_to IS NULL OR membership.effective_to::date > ?)
                ORDER BY t.target_key
                """,
                (rule["theme_id"], as_of, as_of),
            )
        if rule["scope_type"] == "mandate":
            return self.execute_query(
                """
                SELECT DISTINCT t.target_id, t.target_key, t.target_name
                FROM mandate_version version
                JOIN mandate_target_assignment assignment USING (mandate_version_id)
                JOIN investment_target t USING (target_id)
                WHERE version.mandate_id = ?
                  AND version.effective_from::date <= ?
                  AND (version.effective_until IS NULL OR version.effective_until::date > ?)
                  AND assignment.effective_from::date <= ?
                  AND (assignment.effective_until IS NULL OR assignment.effective_until::date > ?)
                  AND assignment.status = 'active'
                ORDER BY t.target_key
                """,
                (rule["mandate_id"], as_of, as_of, as_of, as_of),
            )
        status_clause = " AND watch.status = ?" if rule["watchlist_status"] else ""
        params: tuple[Any, ...] = (rule["watchlist_status"],) if rule["watchlist_status"] else ()
        return self.execute_query(
            """
            SELECT t.target_id, t.target_key, t.target_name
            FROM watchlist_entry watch
            JOIN investment_target t USING (target_id)
            WHERE TRUE
            """
            + status_clause
            + " ORDER BY t.target_key",
            params,
        )

    def evaluations(
        self,
        rule_id: int | None = None,
        *,
        work_state: str | None = None,
        mode: str | None = "live",
    ) -> list[dict[str, Any]]:
        """実運用の判定を返す。既定は作業キューとして使う順序で並べる。

        **過去再生は返さない。** replayはRuleの閾値を校正するための検証処理で、
        人間が対応すべき案件ではない。同じ一覧へ混ぜると、動作確認のために押した
        結果が作業キューを埋める（replayは過去全件を評価するため件数も多い）。

        並びは `evaluation_id` ではなく開示日の降順にする。前者は「人が評価を実行
        した順」であって、案件の新しさを表さない。

        `work_state` は対応状態での絞り込み。
        `actionable` は未対応と、再確認日が到来した保留を指す。保留のうち再確認日が
        未来のものは、今やることではないので既定から外す。
        """
        states = {
            # 一度も確認していない
            "unhandled": "response_count = 0",
            # 未対応、または再確認日が到来した保留
            "actionable": (
                "(response_count = 0"
                " OR (latest_response_disposition = 'deferred'"
                "     AND next_review_on <= CURRENT_DATE))"
            ),
            # 確認したが、再確認日がまだ先
            "deferred": ("latest_response_disposition = 'deferred' AND next_review_on > CURRENT_DATE"),
            # 方針が確定した
            "settled": "latest_response_disposition IN ('accepted', 'overridden')",
        }
        if work_state is not None and work_state not in states:
            raise ValueError(f"未対応の work_state: {work_state}")

        conditions: list[str] = []
        parameters: list[Any] = []
        if mode is not None:
            conditions.append("e.mode = ?")
            parameters.append(mode)
        if rule_id is not None:
            conditions.append("v.rule_id = ?")
            parameters.append(rule_id)
        return self.execute_query(
            """
            SELECT * FROM (
                SELECT e.*, v.rule_id, v.logic_operator,
                       c.metric_key, c.comparison_type, c.operator, c.threshold_value,
                       (SELECT jsonb_agg(jsonb_build_object(
                            'metric_key', condition.metric_key,
                            'comparison_type', condition.comparison_type,
                            'operator', condition.operator,
                            'threshold_value', condition.threshold_value
                        ) ORDER BY condition.position)
                        FROM trigger_rule_condition condition
                        WHERE condition.rule_version_id = v.rule_version_id) AS conditions,
                       t.target_key, t.target_name,
                       (SELECT COUNT(*) FROM trigger_response response
                        WHERE response.evaluation_id = e.evaluation_id) AS response_count,
                       (SELECT response.disposition FROM trigger_response response
                        WHERE response.evaluation_id = e.evaluation_id
                        ORDER BY response.response_id DESC LIMIT 1) AS latest_response_disposition,
                       (SELECT response.next_review_on FROM trigger_response response
                        WHERE response.evaluation_id = e.evaluation_id
                        ORDER BY response.response_id DESC LIMIT 1) AS next_review_on
                FROM trigger_evaluation e
                JOIN trigger_rule_version v USING (rule_version_id)
                JOIN trigger_rule_condition c ON c.rule_version_id = v.rule_version_id AND c.position = 1
                JOIN investment_target t ON t.target_id = e.target_id
                WHERE """
            + (" AND ".join(conditions) if conditions else "TRUE")
            + """
            ) rows
            """
            + (f" WHERE {states[work_state]}" if work_state else "")
            + """
            ORDER BY (evidence->>'actual_disclosed_date')::DATE DESC NULLS LAST, evaluation_id DESC
            LIMIT 200
            """,
            tuple(parameters),
        )

    def save_evaluation(
        self,
        rule_version_id: int,
        target_id: int,
        mode: str,
        as_of: date,
        result: dict[str, Any],
        *,
        orchestration_run_id: str | None = None,
    ) -> bool:
        return (
            self.execute_write(
                """
            INSERT INTO trigger_evaluation
                (rule_version_id, target_id, mode, event_key, as_of_date, status, reason, observed_value,
                 reference_value, evidence, evidence_hash, definition_version, orchestration_run_id,
                 on_fire_decision)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?,
                   on_fire_decision
            FROM trigger_rule_version WHERE rule_version_id = ?
            ON CONFLICT (rule_version_id, mode, event_key) DO NOTHING
        """,
                (
                    rule_version_id,
                    target_id,
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
                    orchestration_run_id,
                    rule_version_id,
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
                (evaluation_id, disposition, override_decision, rationale, next_review_on, actor_type, actor_id)
            VALUES (?, ?, ?, ?, ?, 'human', 'owner')
        """,
            "response_id",
            (evaluation_id, body.disposition, body.override_decision, body.rationale, body.next_review_on),
        )
