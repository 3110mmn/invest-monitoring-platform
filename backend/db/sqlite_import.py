"""Legacy SQLite database import into the canonical PostgreSQL database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from app.database import Connection

TABLES = (
    "strategy",
    "theme",
    "investment_target",
    "data_source",
    "ingestion_run",
    "ingestion_error",
    "investment_target_identifier",
    "theme_investment_target",
)

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "strategy": ("strategy_id",),
    "theme": ("theme_id",),
    "investment_target": ("target_id",),
    "data_source": ("source_id",),
    "ingestion_run": ("ingestion_run_id",),
    "ingestion_error": ("error_id",),
    "investment_target_identifier": ("investment_target_identifier_id",),
    "theme_investment_target": ("membership_id",),
}

IDENTITY_COLUMNS = {
    "strategy": "strategy_id",
    "theme": "theme_id",
    "investment_target": "target_id",
    "data_source": "source_id",
    "ingestion_run": "ingestion_run_id",
    "ingestion_error": "error_id",
    "investment_target_identifier": "investment_target_identifier_id",
    "theme_investment_target": "membership_id",
}

BOOLEAN_COLUMNS = {
    "is_active",
    "is_monitored",
    "is_primary",
    "retryable",
}
DATE_COLUMNS = {
    "requested_from",
    "requested_to",
    "valid_from",
    "valid_to",
    "obs_date",
    "disclosed_date",
    "period_start",
    "period_end",
    "fiscal_year_start",
    "fiscal_year_end",
}
TIME_COLUMNS = {"disclosed_time"}
TIMESTAMP_COLUMNS = {
    "created_at",
    "updated_at",
    "fetched_at",
    "started_at",
    "finished_at",
}


def _convert_value(column: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column in BOOLEAN_COLUMNS:
        return bool(value)
    if column in DATE_COLUMNS and isinstance(value, str):
        return date.fromisoformat(value[:10])
    if column in TIME_COLUMNS and isinstance(value, str):
        return time.fromisoformat(value)
    if column in TIMESTAMP_COLUMNS and isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return value


def _source_tables(source: sqlite3.Connection) -> set[str]:
    rows = source.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _target_columns(target: Connection, table: str) -> set[str]:
    rows = target.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _target_contains_data(target: Connection) -> bool:
    for table in TABLES:
        count = int(target.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        # Alembic seeds the three default strategies; these may be safely upserted.
        if count and not (table == "strategy" and count == 3):
            return True
    return False


def _truncate_target(target: Connection) -> None:
    target.execute("TRUNCATE TABLE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")


def _rows(source: sqlite3.Connection, table: str) -> Iterable[sqlite3.Row]:
    return source.execute(f"SELECT * FROM {table}")


def _upsert_rows(
    source: sqlite3.Connection,
    target: Connection,
    table: str,
) -> int:
    rows = list(_rows(source, table))
    if not rows:
        return 0

    if table == "theme_investment_target":
        values = []
        for row in rows:
            created_at = _convert_value("created_at", row["created_at"])
            updated_at = _convert_value("updated_at", row["updated_at"])
            effective_to = None if bool(row["is_active"]) else updated_at
            values.append(
                (row["theme_id"], row["target_id"], created_at, effective_to, created_at, updated_at)
            )
        target.executemany(
            """
            INSERT INTO theme_investment_target (
                theme_id, target_id, effective_from, effective_to, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (theme_id, target_id) WHERE effective_to IS NULL DO NOTHING
            """,
            values,
        )
        return len(rows)

    target_columns = _target_columns(target, table)
    source_columns = list(rows[0].keys())
    source_for_target = {column: column for column in source_columns if column in target_columns}
    if table == "investment_target" and "is_active" in source_columns:
        source_for_target["is_monitored"] = "is_active"
    columns = list(source_for_target)
    if not columns:
        raise RuntimeError(f"No compatible columns found for {table}")

    primary_keys = PRIMARY_KEYS[table]
    update_columns = [column for column in columns if column not in primary_keys]
    placeholders = ", ".join("?" for _ in columns)
    conflict = ", ".join(primary_keys)
    if update_columns:
        update = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        action = f"DO UPDATE SET {update}"
    else:
        action = "DO NOTHING"
    query = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) {action}"
    )
    values = [
        tuple(
            _convert_value(column, row[source_for_target[column]])
            for column in columns
        )
        for row in rows
    ]
    target.executemany(query, values)
    return len(rows)


def _reset_identity_sequences(target: Connection) -> None:
    for table, column in IDENTITY_COLUMNS.items():
        target.execute(
            "SELECT setval(pg_get_serial_sequence(?, ?), "
            f"COALESCE((SELECT MAX({column}) FROM {table}), 1), "
            f"EXISTS(SELECT 1 FROM {table}))",
            (table, column),
        )


def import_sqlite_database(
    source_path: Path,
    target: Connection,
    *,
    replace: bool = False,
) -> Mapping[str, int]:
    """Import all supported tables in one PostgreSQL transaction.

    A non-empty destination is rejected unless ``replace`` is explicitly set.
    The caller owns the target connection and its transaction lifecycle.
    """
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        missing = set(TABLES) - _source_tables(source)
        if missing:
            raise RuntimeError(
                "SQLite database is missing required tables: " + ", ".join(sorted(missing))
            )

        if replace:
            _truncate_target(target)
        elif _target_contains_data(target):
            raise RuntimeError(
                "PostgreSQL destination contains data; rerun with --replace only after backup"
            )

        counts = {table: _upsert_rows(source, target, table) for table in TABLES}
        _reset_identity_sequences(target)

        for table, expected in counts.items():
            actual_row = target.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()
            actual = int(actual_row["count"])
            if actual != expected:
                raise RuntimeError(
                    f"Row-count mismatch for {table}: SQLite={expected}, PostgreSQL={actual}"
                )
        return counts
    finally:
        source.close()
