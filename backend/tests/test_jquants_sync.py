import argparse

import pytest

from app.etl.loaders import active_jquants_codes
from app.etl.normalizers import target_key_to_jpx_code
from scripts.jquants_sync import (
    find_unmapped_jquants_codes,
    iso_date,
    sync_missing_masters,
)


def test_iso_date_accepts_and_normalizes_valid_date():
    assert iso_date("2026-09-03") == "2026-09-03"


@pytest.mark.parametrize("value", ["hoge", "2026-02-30", "20260903"])
def test_iso_date_rejects_invalid_date(value):
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        iso_date(value)


@pytest.mark.parametrize(
    "target_key, expected",
    [
        ("7203.T", "72030"),
        ("408A.T", "408A0"),
        ("^VIX", None),
        ("AAPL", None),
        ("ABCD.T", None),
    ],
)
def test_target_key_to_jpx_code(target_key, expected):
    """東証銘柄だけ5桁Codeを導出し、対象外はNoneを返す。"""
    assert target_key_to_jpx_code(target_key) == expected


def _add_target(conn, target_key, is_monitored=True):
    return conn.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type, is_monitored) "
        "VALUES (?, ?, 'individual_stock', ?)",
        (target_key, target_key, is_monitored),
    ).lastrowid


def test_unmapped_codes_are_derived_from_monitored_targets(db):
    """対応が無い監視中の日本株だけがCodeとして導出される。"""
    _add_target(db, "7203.T")
    _add_target(db, "408A.T")
    _add_target(db, "9984.T", is_monitored=False)
    _add_target(db, "AAPL")

    assert find_unmapped_jquants_codes(db) == ["408A0", "72030"]


def test_targets_with_existing_mapping_are_excluded(db):
    """既にjpx_codeが対応済みの銘柄は再同期の対象にしない。"""
    target_id = _add_target(db, "7203.T")
    _add_target(db, "6758.T")
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES ('jquants', 'J-Quants')"
    ).lastrowid
    db.execute(
        "INSERT INTO investment_target_identifier "
        "(target_id, source_id, identifier_type, identifier, is_primary) "
        "VALUES (?, ?, 'jpx_code', '72030', TRUE)",
        (target_id, source_id),
    )

    assert find_unmapped_jquants_codes(db) == ["67580"]


def test_expired_mapping_is_treated_as_unmapped(db):
    """有効期間が終了した対応は未対応として再同期の対象になる。"""
    target_id = _add_target(db, "7203.T")
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES ('jquants', 'J-Quants')"
    ).lastrowid
    db.execute(
        "INSERT INTO investment_target_identifier "
        "(target_id, source_id, identifier_type, identifier, valid_from, valid_to, is_primary) "
        "VALUES (?, ?, 'jpx_code', '72030', '2020-01-01', '2020-12-31', TRUE)",
        (target_id, source_id),
    )

    assert find_unmapped_jquants_codes(db) == ["72030"]


class _FakePipeline:
    """指定Codeだけ失敗する `sync_master` の代役。"""

    def __init__(self, failing_codes=()):
        self.failing_codes = set(failing_codes)
        self.calls = []

    def sync_master(self, *, code, date=None):
        self.calls.append(code)
        if code in self.failing_codes:
            raise RuntimeError(f"J-Quants HTTP error: 404 ({code})")
        return {"run_id": 1, "fetched": 1, "loaded": 1, "failed": 0}


def test_sync_missing_masters_continues_after_a_failure(db):
    """1銘柄が失敗しても例外を伝播させず、残りの銘柄を同期する。"""
    _add_target(db, "7203.T")
    _add_target(db, "6758.T")
    _add_target(db, "9984.T")
    pipeline = _FakePipeline(failing_codes={"67580"})

    succeeded, failed = sync_missing_masters(pipeline, db)

    assert pipeline.calls == ["67580", "72030", "99840"]
    assert succeeded == ["72030", "99840"]
    assert failed == ["67580"]


def test_sync_missing_masters_reports_nothing_to_do(db):
    """未対応の銘柄が無ければAPIを呼ばない。"""
    pipeline = _FakePipeline()

    assert sync_missing_masters(pipeline, db) == ([], [])
    assert pipeline.calls == []


def test_active_jquants_codes_uses_only_current_primary_identifiers(db):
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES ('jquants', 'J-Quants')"
    ).lastrowid
    target_id = db.execute(
        """
        INSERT INTO investment_target (target_key, target_name, target_type)
        VALUES ('test', 'Test', 'individual_stock')
        """
    ).lastrowid
    db.executemany(
        """
        INSERT INTO investment_target_identifier
            (target_id, source_id, identifier_type, identifier, is_primary, valid_from, valid_to)
        VALUES (?, ?, 'jpx_code', ?, TRUE, ?, ?)
        """,
        [
            (target_id, source_id, "OLD", "2020-01-01", "2020-12-31"),
            (target_id, source_id, "CURRENT", "2021-01-01", None),
        ],
    )

    assert active_jquants_codes(db) == ["CURRENT"]
