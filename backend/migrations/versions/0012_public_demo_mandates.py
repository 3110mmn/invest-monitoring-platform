"""Add expiry metadata for disposable public-demo mandates.

Revision ID: 0012_public_demo_mandates
Revises: 0011_target_type_instrument
"""

from alembic import op

revision = "0012_public_demo_mandates"
down_revision = "0011_target_type_instrument"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE capital_allocation_mandate ADD COLUMN demo_expires_at TIMESTAMPTZ")
    op.execute("""
        CREATE INDEX idx_public_demo_mandate_expiry
        ON capital_allocation_mandate(demo_expires_at)
        WHERE demo_expires_at IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_public_demo_mandate_expiry")
    op.execute("ALTER TABLE capital_allocation_mandate DROP COLUMN demo_expires_at")
