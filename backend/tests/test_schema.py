import pytest
from psycopg.errors import UniqueViolation

from app.database import Connection


def _insert_source_and_asset(db: Connection) -> tuple[int, int]:
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES (?, ?)",
        ("jquants", "J-Quants"),
    ).lastrowid
    target_id = db.execute(
        """
        INSERT INTO investment_target (target_key, target_name, target_type)
        VALUES (?, ?, ?)
        """,
        ("8697.T", "Japan Exchange Group", "individual_stock"),
    ).lastrowid
    assert source_id is not None and target_id is not None
    return source_id, target_id



def test_investment_target_identifier_is_unique_for_source_and_validity_period(db):
    source_id, target_id = _insert_source_and_asset(db)
    values = (target_id, source_id, "jpx_code", "86970", True)
    sql = """
        INSERT INTO investment_target_identifier (
            target_id, source_id, identifier_type, identifier, is_primary
        ) VALUES (?, ?, ?, ?, ?)
    """
    db.execute(sql, values)

    with pytest.raises(UniqueViolation):
        db.execute(sql, values)




def test_schema_does_not_persist_derived_direction(db):
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'market_price_observation'"
    ).fetchall()
    assert "direction" not in {row["column_name"] for row in rows}


