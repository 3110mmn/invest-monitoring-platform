from datetime import UTC, datetime

from app.database import Connection
from app.etl.models import InvestmentTargetMasterRecord, ValidationIssue


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_data_source(conn: Connection, key: str, name: str, base_url: str | None = None) -> int:
    conn.execute(
        """
        INSERT INTO data_source (source_key, source_name, base_url)
        VALUES (?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_name = excluded.source_name,
            base_url = COALESCE(excluded.base_url, data_source.base_url),
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, name, base_url),
    )
    row = conn.execute("SELECT source_id FROM data_source WHERE source_key = ?", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"data_source was not created: {key}")
    return int(row["source_id"])


def upsert_investment_target_master(
    conn: Connection, source_id: int, records: list[InvestmentTargetMasterRecord]
) -> int:
    now = utc_now()
    for record in records:
        conn.execute(
            """
            INSERT INTO investment_target (
                target_key, target_name, target_type, market, currency, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_key) DO UPDATE SET
                target_name = excluded.target_name,
                target_type = COALESCE(excluded.target_type, investment_target.target_type),
                market = COALESCE(excluded.market, investment_target.market),
                currency = excluded.currency,
                updated_at = excluded.updated_at
            """,
            (record.target_key, record.target_name, record.target_type, record.market, record.currency, now, now),
        )
        target_row = conn.execute(
            "SELECT target_id FROM investment_target WHERE target_key = ?", (record.target_key,)
        ).fetchone()
        if target_row is None:
            raise RuntimeError(f"investment_target was not created: {record.target_key}")
        target_id = int(target_row["target_id"])
        conn.execute(
            """
            INSERT INTO investment_target_identifier (target_id, source_id, identifier_type, identifier, is_primary)
            VALUES (?, ?, 'jpx_code', ?, TRUE)
            ON CONFLICT(source_id, identifier_type, identifier, valid_from) DO UPDATE SET
                target_id = excluded.target_id, is_primary = TRUE, updated_at = CURRENT_TIMESTAMP
            """,
            (target_id, source_id, record.jpx_code),
        )
    return len(records)


def active_jquants_codes(conn: Connection) -> list[str]:
    """有効な投資対象に紐づく、現在有効なJ-Quants Codeを返す。"""
    return [
        str(row["identifier"])
        for row in conn.execute(
            """
            SELECT DISTINCT ai.identifier
            FROM investment_target_identifier ai
            JOIN investment_target a ON a.target_id = ai.target_id
            JOIN data_source ds ON ds.source_id = ai.source_id
            WHERE ds.source_key = 'jquants'
              AND ai.identifier_type = 'jpx_code'
              AND ai.is_primary = TRUE
              AND a.is_monitored = TRUE
              AND ai.valid_from <= CURRENT_DATE
              AND (ai.valid_to IS NULL OR ai.valid_to >= CURRENT_DATE)
            ORDER BY ai.identifier
            """
        ).fetchall()
    ]


def resolve_target_id(conn: Connection, source_id: int, jpx_code: str) -> int | None:
    row = conn.execute(
        """
        SELECT target_id FROM investment_target_identifier
        WHERE source_id = ? AND identifier_type = 'jpx_code' AND identifier = ?
          AND valid_from <= CURRENT_DATE AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
        ORDER BY is_primary DESC, valid_from DESC LIMIT 1
        """,
        (source_id, jpx_code),
    ).fetchone()
    return int(row["target_id"]) if row else None


def record_ingestion_errors(conn: Connection, run_id: int, stage: str, issues: list[ValidationIssue]) -> None:
    conn.executemany(
        """
        INSERT INTO ingestion_error (
            ingestion_run_id, entity_type, entity_key, stage,
            error_type, error_message, retryable
        ) VALUES (?, 'record', ?, ?, ?, ?, ?)
        """,
        [(run_id, item.entity_key, stage, item.error_type, item.message, item.retryable) for item in issues],
    )
