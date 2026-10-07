"""Constrain mandate review cycles and support a custom cycle label.

Revision ID: 0006_mandate_review_cycle
Revises: 0005_capital_allocation_mandate
"""

from alembic import op

revision = "0006_mandate_review_cycle"
down_revision = "0005_capital_allocation_mandate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE mandate_version ADD COLUMN review_cycle_custom TEXT")
    op.execute("""
        ALTER TABLE mandate_version
        ADD CONSTRAINT ck_mandate_review_cycle
        CHECK (
            review_cycle IS NULL OR review_cycle IN (
                'monthly', 'quarterly', 'semiannual', 'annual', 'ad_hoc', 'other'
            )
        )
    """)
    op.execute("""
        ALTER TABLE mandate_version
        ADD CONSTRAINT ck_mandate_review_cycle_custom
        CHECK (
            (review_cycle = 'other' AND NULLIF(BTRIM(review_cycle_custom), '') IS NOT NULL)
            OR (review_cycle IS DISTINCT FROM 'other' AND review_cycle_custom IS NULL)
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE mandate_version DROP CONSTRAINT ck_mandate_review_cycle_custom")
    op.execute("ALTER TABLE mandate_version DROP CONSTRAINT ck_mandate_review_cycle")
    op.execute("ALTER TABLE mandate_version DROP COLUMN review_cycle_custom")
