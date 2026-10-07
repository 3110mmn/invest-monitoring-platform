"""rawからParquetを組み立てる変換を確認する。

Parquetは分析層の入口になるため、ここで取りこぼすとDWH側に一次観測が存在しなくなる。
"""

import re
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from scripts.build_parquet import (
    PRICE_BUILDERS,
    build_price_rows,
    jpx_code_to_target_key,
    write_year_partitions,
)


def _price(code: str, day: str) -> dict:
    return {
        "Date": day, "Code": code,
        "O": 101.0, "H": 111.0, "L": 91.0, "C": 106.0,
        "Vo": 900.0, "Va": 95000.0, "AdjFactor": 1.0,
        "MktCap": 123456.0, "ExRT": "1", "UL": "1", "LL": "0",
        "AdjO": 100.0, "AdjH": 110.0, "AdjL": 90.0, "AdjC": 105.0, "AdjVo": 1000.0,
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [("72030", "7203.T"), ("408A0", "408A.T"), ("13010", "1301.T")],
)
def test_standard_codes_map_to_target_key(code, expected):
    assert jpx_code_to_target_key(code) == expected


@pytest.mark.parametrize("code", ["25935", "94346", "7203", "720300"])
def test_non_standard_codes_are_not_guessed(code):
    """優先株など末尾が0でないコードは推測しない。誤った銘柄へ紐づけないため。"""
    assert jpx_code_to_target_key(code) is None


def test_unmapped_securities_are_kept_not_dropped():
    """target_keyを引けなくても行は残す。実在する証券の価格を分析から消さない。"""
    records = [_price("72030", "2026-07-01"), _price("25935", "2026-07-01")]

    rows, skipped, unmapped = build_price_rows(records, run_id=1)

    assert len(rows) == 2
    assert skipped == 0
    assert unmapped == 1
    unmapped_row = next(r for r in rows if r["target_key"] is None)
    assert unmapped_row["jpx_code"] == "25935", "jpx_codeがbusiness keyとして残る"


def test_rows_carry_business_keys_not_surrogate_ids():
    """PostgreSQLのtarget_id / source_idを書かない。分析層をServing DBへ依存させない。"""
    rows, _, _ = build_price_rows([_price("72030", "2026-07-01")], run_id=1)

    assert rows[0]["target_key"] == "7203.T"
    assert rows[0]["source_key"] == "jquants"
    assert "target_id" not in rows[0]
    assert "source_id" not in rows[0]


def test_ingestion_run_is_carried_into_parquet():
    """来歴をParquetへ持ち越す。どの取込で得た値かを分析側でも辿れるようにする。"""
    rows, _, _ = build_price_rows([_price("72030", "2026-07-01")], run_id=42)

    assert rows[0]["ingestion_run_id"] == 42


def test_price_basis_is_recorded_as_adjusted():
    """調整済みと未調整を混ぜない。J-QuantsのAdj*は調整済み。"""
    rows, _, _ = build_price_rows([_price("72030", "2026-07-01")], run_id=1)

    assert rows[0]["price_basis"] == "adjusted"


def test_standard_daily_fields_are_kept_in_the_observed_lake():
    """rawから再構築せずに流動性・時価総額・権利落ちを分析できるようにする。"""
    rows, _, _ = build_price_rows([_price("72030", "2026-07-01")], run_id=1)

    row = rows[0]
    assert row["raw_open_price"] == pytest.approx(101.0)
    assert row["raw_high_price"] == pytest.approx(111.0)
    assert row["raw_low_price"] == pytest.approx(91.0)
    assert row["raw_close_price"] == pytest.approx(106.0)
    assert row["raw_volume"] == pytest.approx(900.0)
    assert row["turnover_value"] == pytest.approx(95000.0)
    assert row["adjustment_factor"] == pytest.approx(1.0)
    assert row["market_cap_million_yen"] == pytest.approx(123456.0)
    assert row["ex_rights_type"] == "1"
    assert row["upper_limit_flag"] is True
    assert row["lower_limit_flag"] is False


def test_unknown_limit_flag_rejects_the_record():
    record = {**_price("72030", "2026-07-01"), "UL": "unknown"}

    rows, skipped, _ = build_price_rows([record], run_id=1)

    assert rows == []
    assert skipped == 1


def test_partitions_are_written_per_year(tmp_path):
    """年で分ける。銘柄×月まで割るとsmall filesが大量に出る。"""
    rows, _, _ = build_price_rows(
        [_price("72030", "2025-12-01"), _price("72030", "2026-01-05")], run_id=1
    )

    written = write_year_partitions(rows, tmp_path / "out")

    assert written == {2025: 1, 2026: 1}
    table = pq.read_table(tmp_path / "out" / "year=2026" / "part-0.parquet")
    assert table.num_rows == 1


def test_every_price_ingestion_has_a_parquet_builder():
    """価格をrawへ残す取込種別は、すべてParquetへ変換できること。

    価格はParquetからしか読まないため、対応表に載っていない取込種別のrawは分析層へ
    届かない。取得してPostgreSQLに入っているのに画面に出ない、という気づきにくい
    状態になる。実際に `jquants_daily_prices_v1` が漏れていた。
    """
    from app.etl import pipeline

    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    price_job_types = {
        match
        for match in re.findall(r'job_type = "(jquants_[a-z_]*prices[a-z_0-9]*)"', source)
    }

    assert price_job_types, "pipelineから価格の取込種別を読み取れていない"
    assert price_job_types <= set(PRICE_BUILDERS), (
        f"変換方法が無い取込種別: {sorted(price_job_types - set(PRICE_BUILDERS))}"
    )
