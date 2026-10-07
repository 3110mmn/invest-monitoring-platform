"""Classify investment targets by tradable instrument form.

Revision ID: 0011_target_type_instrument
Revises: 0010_mandate_assignment_history
"""

from alembic import op

revision = "0011_target_type_instrument"
down_revision = "0010_mandate_assignment_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # REIT is treated as an individually tradable listed security in the MVP.
    # An index or a commodity is not itself the tradable wrapper, so avoid
    # guessing whether an old row represents an ETF, fund, or another product.
    op.execute("ALTER TABLE investment_target DROP CONSTRAINT investment_target_target_type_check")
    op.execute("""
        UPDATE investment_target
        SET target_type = CASE
            WHEN target_type = 'reit' THEN 'individual_stock'
            WHEN target_type IN ('index', 'commodity') THEN NULL
            ELSE target_type
        END
        WHERE target_type IN ('reit', 'index', 'commodity')
    """)
    op.execute("""
        ALTER TABLE investment_target
        ADD CONSTRAINT investment_target_target_type_check
        CHECK (target_type IS NULL OR target_type IN (
            'individual_stock', 'etf', 'mutual_fund', 'bond'
        ))
    """)


def downgrade() -> None:
    # Existing rows retain the normalized classification. The former broader
    # vocabulary is restored, but exact index/commodity values cannot be inferred.
    op.execute("ALTER TABLE investment_target DROP CONSTRAINT investment_target_target_type_check")
    op.execute("""
        ALTER TABLE investment_target
        ADD CONSTRAINT investment_target_target_type_check
        CHECK (target_type IS NULL OR target_type IN (
            'individual_stock', 'etf', 'mutual_fund', 'reit', 'bond', 'index', 'commodity'
        ))
    """)
