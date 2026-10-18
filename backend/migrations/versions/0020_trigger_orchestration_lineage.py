"""Link scheduled Trigger evaluations to their orchestrator run.

Revision ID: 0020_trigger_airflow_run
Revises: 0019_trigger_multiple_conditions
"""

from alembic import op

revision = "0020_trigger_airflow_run"
down_revision = "0019_trigger_multiple_conditions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trigger_evaluation ADD COLUMN orchestration_run_id TEXT")
    op.execute(
        "CREATE INDEX idx_trigger_evaluation_orchestration_run "
        "ON trigger_evaluation(orchestration_run_id) WHERE orchestration_run_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX idx_trigger_evaluation_orchestration_run")
    op.execute("ALTER TABLE trigger_evaluation DROP COLUMN orchestration_run_id")
