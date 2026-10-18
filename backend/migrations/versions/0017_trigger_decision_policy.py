"""Let a Trigger carry a precommitted decision policy.

Revision ID: 0017_trigger_decision_policy
Revises: 0016_trigger_scope_assignment
"""

from alembic import op

revision = "0017_trigger_decision_policy"
down_revision = "0016_trigger_scope_assignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trigger_rule_version DROP CONSTRAINT trigger_rule_version_check")
    op.execute("ALTER TABLE trigger_rule_version DROP CONSTRAINT trigger_rule_version_operator_check")
    op.execute("""
        ALTER TABLE trigger_rule_version
        ADD CONSTRAINT trigger_rule_version_operator_check
        CHECK (operator IN ('lt', 'lte', 'gt', 'gte', 'crosses_below', 'crosses_above'))
    """)
    op.execute("""
        ALTER TABLE trigger_rule_version
        ADD CONSTRAINT trigger_rule_version_check
        CHECK ((comparison_type = 'sign_transition'
                AND operator IN ('crosses_below', 'crosses_above')
                AND threshold_value IS NULL)
            OR (comparison_type <> 'sign_transition'
                AND operator IN ('lt', 'lte', 'gt', 'gte')
                AND threshold_value IS NOT NULL))
    """)
    op.execute("""
        ALTER TABLE trigger_rule_version
        ADD COLUMN on_fire_decision TEXT NOT NULL DEFAULT 'investigate'
            CHECK (on_fire_decision IN
                ('investigate', 'review_allocation', 'buy', 'sell', 'hold', 'no_action'))
    """)
    op.execute("""
        ALTER TABLE trigger_evaluation
        ADD COLUMN on_fire_decision TEXT NOT NULL DEFAULT 'investigate'
            CHECK (on_fire_decision IN
                ('investigate', 'review_allocation', 'buy', 'sell', 'hold', 'no_action'))
    """)
    op.execute("""
        UPDATE trigger_evaluation evaluation
        SET on_fire_decision = version.on_fire_decision
        FROM trigger_rule_version version
        WHERE version.rule_version_id = evaluation.rule_version_id
    """)

    # Existing rows were explicit human choices, so preserve them as overrides of
    # the new default policy rather than pretending the default was accepted.
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_response_type_check")
    op.execute("ALTER TABLE trigger_response ADD COLUMN disposition TEXT")
    op.execute("ALTER TABLE trigger_response ADD COLUMN override_decision TEXT")
    op.execute("""
        UPDATE trigger_response
        SET disposition = 'overridden', override_decision = response_type
    """)
    op.execute("ALTER TABLE trigger_response ALTER COLUMN disposition SET NOT NULL")
    op.execute("ALTER TABLE trigger_response DROP COLUMN response_type")
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_disposition_check
            CHECK (disposition IN ('accepted', 'overridden', 'deferred'))
    """)
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_override_decision_check
            CHECK (override_decision IS NULL OR override_decision IN
                ('investigate', 'review_allocation', 'buy', 'sell', 'hold', 'no_action'))
    """)
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_override_shape_check
            CHECK ((disposition = 'overridden' AND override_decision IS NOT NULL)
                OR (disposition <> 'overridden' AND override_decision IS NULL))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE trigger_response ADD COLUMN response_type TEXT")
    op.execute("""
        UPDATE trigger_response response
        SET response_type = CASE
            WHEN response.disposition = 'overridden'
                 AND response.override_decision IN ('investigate', 'review_allocation', 'no_action')
                THEN response.override_decision
            WHEN response.disposition <> 'overridden'
                 AND evaluation.on_fire_decision IN ('investigate', 'review_allocation', 'no_action')
                THEN evaluation.on_fire_decision
            ELSE 'no_action'
        END
        FROM trigger_evaluation evaluation
        WHERE evaluation.evaluation_id = response.evaluation_id
    """)
    op.execute("ALTER TABLE trigger_response ALTER COLUMN response_type SET NOT NULL")
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_response_type_check
        CHECK (response_type IN ('investigate', 'review_allocation', 'no_action'))
    """)
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_override_shape_check")
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_override_decision_check")
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_disposition_check")
    op.execute("ALTER TABLE trigger_response DROP COLUMN override_decision")
    op.execute("ALTER TABLE trigger_response DROP COLUMN disposition")
    op.execute("ALTER TABLE trigger_evaluation DROP COLUMN on_fire_decision")
    op.execute("ALTER TABLE trigger_rule_version DROP COLUMN on_fire_decision")
    op.execute("ALTER TABLE trigger_rule_version DROP CONSTRAINT trigger_rule_version_check")
    op.execute("ALTER TABLE trigger_rule_version DROP CONSTRAINT trigger_rule_version_operator_check")
    op.execute("""
        ALTER TABLE trigger_rule_version
        ADD CONSTRAINT trigger_rule_version_operator_check
        CHECK (operator IN ('lte', 'gte', 'crosses_below', 'crosses_above'))
    """)
    op.execute("""
        ALTER TABLE trigger_rule_version
        ADD CONSTRAINT trigger_rule_version_check
        CHECK ((comparison_type = 'sign_transition'
                AND operator IN ('crosses_below', 'crosses_above')
                AND threshold_value IS NULL)
            OR (comparison_type <> 'sign_transition'
                AND operator IN ('lte', 'gte')
                AND threshold_value IS NOT NULL))
    """)
