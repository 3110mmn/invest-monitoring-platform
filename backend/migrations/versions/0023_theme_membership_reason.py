"""Record reasons for Theme membership additions and removals.

Revision ID: 0023_theme_membership_reason
Revises: 0022_theme_membership_import
"""

from alembic import op

revision = "0023_theme_membership_reason"
down_revision = "0022_theme_membership_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE theme_investment_target
        ADD COLUMN added_reason TEXT,
        ADD COLUMN removed_reason TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE theme_investment_target DROP COLUMN removed_reason")
    op.execute("ALTER TABLE theme_investment_target DROP COLUMN added_reason")
