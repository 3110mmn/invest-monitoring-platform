"""Clarify that Trigger responses record human handling, not Rule maintenance."""

from alembic import op

revision = "0015_trigger_response_vocabulary"
down_revision = "0014_trigger_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_response_type_check")
    op.execute("UPDATE trigger_response SET response_type = 'maintain' WHERE response_type IN ('continue', 'hold')")
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_response_type_check
        CHECK (response_type IN ('maintain', 'investigate', 'review_allocation', 'stop_monitoring'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE trigger_response DROP CONSTRAINT trigger_response_response_type_check")
    op.execute("UPDATE trigger_response SET response_type = 'continue' WHERE response_type = 'maintain'")
    op.execute("UPDATE trigger_response SET response_type = 'hold' WHERE response_type = 'stop_monitoring'")
    op.execute("""
        ALTER TABLE trigger_response
        ADD CONSTRAINT trigger_response_response_type_check
        CHECK (response_type IN ('continue', 'investigate', 'review_allocation', 'hold'))
    """)
