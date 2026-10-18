"""Require a review date when a trigger response is deferred.

Revision ID: 0018_deferred_review_date
Revises: 0017_trigger_decision_policy
"""

from alembic import op

revision = "0018_deferred_review_date"
down_revision = "0017_trigger_decision_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`deferred` に再確認日を必須にする。

    **再確認日が無い保留は、作業キューから永久に消える。** 要対応の判定は
    `deferred AND next_review_on <= CURRENT_DATE` なので、NULLだと二度と
    条件に合致しない。`overridden` が `override_decision` を要求するのと同じ
    理由で、状態と必要な情報を揃える。

    既存の `deferred` で日付が無い行は、当日を入れて要対応へ戻す。保留の意図は
    分からないが、見落とすより人の目に触れる方を選ぶ。
    """
    op.execute("""
        UPDATE trigger_response
        SET next_review_on = CURRENT_DATE
        WHERE disposition = 'deferred' AND next_review_on IS NULL
    """)
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_deferred_review_date_check
            CHECK (disposition <> 'deferred' OR next_review_on IS NOT NULL)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE trigger_response
        DROP CONSTRAINT trigger_response_deferred_review_date_check
    """)
