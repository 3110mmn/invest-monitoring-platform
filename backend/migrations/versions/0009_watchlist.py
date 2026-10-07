"""Separate selected target identity from watchlist membership.

Revision ID: 0009_watchlist
Revises: 0008_remove_legacy_strategy
"""

from alembic import op

revision = "0009_watchlist"
down_revision = "0008_remove_legacy_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE watchlist_entry (
            target_id BIGINT PRIMARY KEY REFERENCES investment_target(target_id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('considering', 'monitoring', 'paused')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("""
        INSERT INTO watchlist_entry (target_id, status)
        SELECT target_id, CASE WHEN is_monitored THEN 'monitoring' ELSE 'paused' END
        FROM investment_target
    """)
    op.execute("ALTER TABLE investment_target DROP COLUMN is_monitored")


def downgrade() -> None:
    op.execute("ALTER TABLE investment_target ADD COLUMN is_monitored BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("""
        UPDATE investment_target AS t SET is_monitored = (w.status = 'monitoring')
        FROM watchlist_entry AS w WHERE w.target_id = t.target_id
    """)
    op.execute("DROP TABLE watchlist_entry")
