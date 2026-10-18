FROM apache/airflow:3.3.2-python3.11

USER root
RUN python -m venv /opt/project-venv \
    && python -m venv /opt/dbt-venv \
    && chown -R airflow:0 /opt/project-venv /opt/dbt-venv

USER airflow
COPY backend/requirements.txt /tmp/requirements-backend.txt
COPY backend/requirements-dbt.txt /tmp/requirements-dbt.txt
RUN /opt/project-venv/bin/pip install --no-cache-dir -r /tmp/requirements-backend.txt \
    && /opt/dbt-venv/bin/pip install --no-cache-dir -r /tmp/requirements-dbt.txt
