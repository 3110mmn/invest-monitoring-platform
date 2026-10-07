"""Allow multiple effective-period versions of mandate target assignments.

Revision ID: 0010_mandate_assignment_history
Revises: 0009_watchlist
"""

from alembic import op

revision = "0010_mandate_assignment_history"
down_revision = "0009_watchlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep at most one open/current assignment per target within a mandate version,
    # while allowing closed historical rows to remain queryable.
    op.execute(
        "ALTER TABLE mandate_target_assignment "
        "DROP CONSTRAINT mandate_target_assignment_mandate_version_id_target_id_key"
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_mandate_assignment_current
        ON mandate_target_assignment(mandate_version_id, target_id)
        WHERE effective_until IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_mandate_assignment_history
        ON mandate_target_assignment(mandate_version_id, target_id, effective_from DESC)
    """)


def downgrade() -> None:
    # Multiple historical rows per target cannot be represented by the former
    # unique constraint without deleting history.
    raise NotImplementedError(
        "Assignment history cannot be downgraded without discarding historical rows"
    )
