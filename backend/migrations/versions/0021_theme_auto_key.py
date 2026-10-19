"""Generate stable Theme keys in PostgreSQL.

Revision ID: 0021_theme_auto_key
Revises: 0020_trigger_orchestration_lineage
"""

from alembic import op

revision = "0021_theme_auto_key"
down_revision = "0020_trigger_airflow_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE theme_key_seq")
    op.execute("""
        SELECT setval(
            'theme_key_seq',
            COALESCE(MAX(substring(theme_key FROM '^theme-([0-9]+)$')::BIGINT), 0) + 1,
            FALSE
        )
        FROM theme
    """)
    op.execute("""
        ALTER TABLE theme
        ALTER COLUMN theme_key SET DEFAULT (
            'theme-' || LPAD(nextval('theme_key_seq')::TEXT, 6, '0')
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE theme ALTER COLUMN theme_key DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS theme_key_seq")
