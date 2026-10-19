from datetime import UTC, datetime

from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.theme_membership_import_repository import (
    ThemeMembershipImportRepository,
)
from scripts.import_theme_members import calculate_membership_diff, parse_snapshot_at


def test_replace_diff_adds_and_removes_while_append_only_adds():
    current = [
        {"target_key": "A"},
        {"target_key": "B"},
    ]
    incoming = [
        {"target_key": "B"},
        {"target_key": "C"},
    ]

    *_, append_added, append_removed, append_unchanged = calculate_membership_diff(
        current, incoming, "append"
    )
    *_, replace_added, replace_removed, replace_unchanged = calculate_membership_diff(
        current, incoming, "replace"
    )

    assert (append_added, append_removed, append_unchanged) == (["C"], [], ["B"])
    assert (replace_added, replace_removed, replace_unchanged) == (["C"], ["A"], ["B"])


def test_snapshot_date_is_normalized_to_utc_midnight():
    assert parse_snapshot_at("2026-10-19") == datetime(2026, 10, 19, tzinfo=UTC)


def test_import_run_is_linked_to_membership_addition_and_removal(db):
    theme_id = db.execute(
        "INSERT INTO theme (theme_key, theme_name) VALUES ('reit', 'J-REIT')"
    ).lastrowid
    target_id = db.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type) "
        "VALUES ('8951.T', 'REIT A', 'individual_stock')"
    ).lastrowid
    snapshot_at = datetime(2026, 10, 19, tzinfo=UTC)
    imports = ThemeMembershipImportRepository(db)
    targets = InvestmentTargetRepository(db)
    add_import_id = imports.create({
        "theme_id": theme_id,
        "snapshot_at": snapshot_at,
        "import_mode": "append",
        "artifact_name": "reit.csv",
        "artifact_ref": "取り込みデータ/reit.csv",
        "artifact_hash": "a" * 64,
        "actor": "owner",
        "input_count": 1,
        "added_count": 1,
        "removed_count": 0,
        "unchanged_count": 0,
    })
    targets.add_theme_investment_target(
        theme_id,
        target_id,
        effective_from=snapshot_at,
        import_id=add_import_id,
        reason="REIT一覧に掲載されたため",
    )
    remove_at = datetime(2027, 1, 1, tzinfo=UTC)
    remove_import_id = imports.create({
        "theme_id": theme_id,
        "snapshot_at": remove_at,
        "import_mode": "replace",
        "artifact_name": "reit-2027.csv",
        "artifact_ref": "取り込みデータ/reit-2027.csv",
        "artifact_hash": "b" * 64,
        "actor": "owner",
        "input_count": 0,
        "added_count": 0,
        "removed_count": 1,
        "unchanged_count": 0,
    })
    targets.deactivate_theme_investment_target(
        theme_id,
        target_id,
        effective_to=remove_at,
        import_id=remove_import_id,
        reason="次回スナップショットから掲載がなくなったため",
    )
    db.commit()

    row = db.execute(
        "SELECT effective_from, effective_to, added_import_id, removed_import_id, "
        "added_reason, removed_reason "
        "FROM theme_investment_target WHERE theme_id = ? AND target_id = ?",
        (theme_id, target_id),
    ).fetchone()

    assert row["effective_from"] == snapshot_at
    assert row["effective_to"] == remove_at
    assert row["added_import_id"] == add_import_id
    assert row["removed_import_id"] == remove_import_id
    assert row["added_reason"] == "REIT一覧に掲載されたため"
    assert row["removed_reason"] == "次回スナップショットから掲載がなくなったため"
