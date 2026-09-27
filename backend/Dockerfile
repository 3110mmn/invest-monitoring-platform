FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATABASE_READ_ONLY=true \
    MANAGEMENT_API_ENABLED=false

WORKDIR /app/backend

RUN groupadd --system appgroup \
    && useradd --system --gid appgroup --home-dir /nonexistent appuser

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=appuser:appgroup backend/app ./app
COPY --chown=appuser:appgroup backend/analytics ./analytics
COPY --chown=appuser:appgroup backend/db ./db
COPY --chown=appuser:appgroup backend/migrations ./migrations
COPY --chown=appuser:appgroup backend/alembic.ini ./alembic.ini

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=2)"

CMD ["python", "-m", "app.server"]
