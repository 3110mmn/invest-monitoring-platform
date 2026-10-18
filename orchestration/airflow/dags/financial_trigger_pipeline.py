"""Daily disclosure-to-Trigger operational pipeline."""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

PROJECT_ROOT = "/opt/airflow/project"
PYTHON = "/opt/project-venv/bin/python"
DBT = "/opt/dbt-venv/bin/dbt"

default_args = {
    "owner": "investment-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
}

with DAG(
    dag_id="financial_disclosure_trigger_pipeline",
    description="J-Quants raw -> GCS -> BigQuery -> dbt quality gate -> operational Trigger",
    schedule="0 18 * * 1-5",
    start_date=pendulum.datetime(2026, 10, 1, tz="Asia/Tokyo"),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["financials", "trigger", "operational"],
    params={
        "archive_from": "",
        "archive_to": "",
        "as_of_date": "",
        "evaluate_existing": False,
    },
) as dag:
    fetch_disclosures = BashOperator(
        task_id="fetch_disclosures",
        cwd=PROJECT_ROOT,
        bash_command="""
        set -euo pipefail
        if [ -n "$ARCHIVE_FROM" ] || [ -n "$ARCHIVE_TO" ]; then
          test -n "$ARCHIVE_FROM" && test -n "$ARCHIVE_TO"
          "$PYTHON" backend/scripts/jquants_sync.py archive-financials \
            --from "$ARCHIVE_FROM" --to "$ARCHIVE_TO"
        else
          "$PYTHON" backend/scripts/jquants_sync.py archive-financials --catch-up
        fi
        """,
        env={
            "PYTHON": PYTHON,
            "ARCHIVE_FROM": "{{ params.archive_from }}",
            "ARCHIVE_TO": "{{ params.archive_to }}",
        },
        append_env=True,
    )

    archive_raw_to_gcs = BashOperator(
        task_id="archive_raw_to_gcs",
        cwd=PROJECT_ROOT,
        bash_command='"$PYTHON" backend/scripts/publish_raw.py',
        env={"PYTHON": PYTHON},
        append_env=True,
    )

    load_bigquery = BashOperator(
        task_id="load_bigquery",
        cwd=PROJECT_ROOT,
        bash_command="""
        set -euo pipefail
        skip_exit_code=99
        if [ "$EVALUATE_EXISTING" = "true" ]; then
          skip_exit_code=0
        fi
        "$PYTHON" backend/scripts/load_jquants_bigquery.py \
          --only-new --skip-exit-code "$skip_exit_code"
        """,
        env={
            "PYTHON": PYTHON,
            "EVALUATE_EXISTING": (
                "{{ 'true' if params.evaluate_existing else 'false' }}"
            ),
        },
        append_env=True,
        # Scheduled runs skip downstream work when there is no new raw. A manual
        # verification run can continue against the facts already in BigQuery.
        skip_on_exit_code=99,
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        cwd=PROJECT_ROOT,
        bash_command=(
            '"$DBT" run --project-dir analytics/dbt --profiles-dir analytics/dbt'
        ),
        env={"DBT": DBT},
        append_env=True,
    )

    dbt_test = BashOperator(
        task_id="dbt_test_quality_gate",
        cwd=PROJECT_ROOT,
        bash_command=(
            '"$DBT" test --project-dir analytics/dbt --profiles-dir analytics/dbt'
        ),
        env={"DBT": DBT},
        append_env=True,
    )

    evaluate_trigger_rules = BashOperator(
        task_id="evaluate_trigger_rules",
        cwd=PROJECT_ROOT,
        bash_command="""
        set -euo pipefail
        effective_date="$AS_OF_DATE"
        if [ -z "$effective_date" ]; then effective_date="$DATA_INTERVAL_END"; fi
        "$PYTHON" backend/scripts/evaluate_triggers.py \
          --as-of "$effective_date" \
          --orchestration-run-id "$ORCHESTRATION_RUN_ID"
        """,
        env={
            "PYTHON": PYTHON,
            "AS_OF_DATE": "{{ params.as_of_date }}",
            "DATA_INTERVAL_END": "{{ data_interval_end.in_timezone('Asia/Tokyo').to_date_string() }}",
            "ORCHESTRATION_RUN_ID": "{{ run_id }}",
        },
        append_env=True,
    )

    refresh_dashboard_queue = BashOperator(
        task_id="refresh_dashboard_queue",
        cwd=PROJECT_ROOT,
        bash_command='"$PYTHON" backend/scripts/report_trigger_queue.py',
        env={"PYTHON": PYTHON},
        append_env=True,
    )

    (
        fetch_disclosures
        >> archive_raw_to_gcs
        >> load_bigquery
        >> dbt_run
        >> dbt_test
        >> evaluate_trigger_rules
        >> refresh_dashboard_queue
    )
