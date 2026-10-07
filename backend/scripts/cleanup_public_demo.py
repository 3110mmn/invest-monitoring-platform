"""Delete expired, disposable mandate records from the dedicated public demo DB."""

import os
from urllib.parse import urlparse

import psycopg


def require_demo_database(database_url: str) -> None:
    username = urlparse(database_url).username
    if username != "invest_demo_cleanup":
        raise RuntimeError(
            "Refusing cleanup: DATABASE_URL must use the dedicated invest_demo_cleanup role"
        )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    require_demo_database(database_url)

    with psycopg.connect(database_url) as connection:
        cursor = connection.execute("""
            DELETE FROM capital_allocation_mandate
            WHERE demo_expires_at IS NOT NULL
              AND demo_expires_at <= CURRENT_TIMESTAMP
        """)
        print(f"Deleted {cursor.rowcount} expired public demo mandate(s).")


if __name__ == "__main__":
    main()
