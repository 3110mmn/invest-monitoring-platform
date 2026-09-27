"""財務サマリーのParquet変換を確認する。

価格と違い、財務は同じ銘柄・同じ期に複数の開示がある（訂正、予想修正）。
開示番号が別なら別レコードとして残す必要がある。
"""

import pyarrow as pa
import pytest

from app.etl.normalizers import (
    FINANCIAL_INT_FIELDS,
    FINANCIAL_REAL_FIELDS,
    NON_CONSOLIDATED_INT_FIELDS,
    NON_CONSOLIDATED_REAL_FIELDS,
    SHARED_INT_FIELDS,
    SHARED_REAL_FIELDS,
)
from app.models.financial import FinancialSummaryValues
from scripts.build_financial_parquet import (
    DISCLOSURE_FIELDS,
    build_financial_rows,
    build_schema,
    deduplicate,
    jpx_code_to_target_key,
)

SCHEMA = build_schema()


def _raw(code="72030", number="1", date="2026-05-12", **overrides) -> dict:
    record = {
        "Code": code,
        "DiscNo": number,
        "DiscDate": date,
        "DiscTime": "15:00",
        "DocType": "FYFinancialStatements_Consolidated_JP",
        "CurPerType": "FY",
        "Sales": "1000",
        "OP": "100",
    }
    record.update(overrides)
    return record


def test_schema_covers_the_financial_value_columns():
    """財務値の列は normalizers の定義から引く。手で並べると二重管理になる。"""
    names = set(SCHEMA.names)

    assert "revenue" in names
    assert "forecast_revenue" in names
    assert "next_forecast_revenue" in names
    assert "eps" in names


def test_rows_carry_business_keys_not_disclosure_id():
    """PostgreSQLの disclosure_id を書かない。分析層をServing DBへ依存させない。"""
    rows, _, _ = build_financial_rows([_raw()], run_id=1, schema=SCHEMA)

    assert rows[0]["jpx_code"] == "72030"
    assert rows[0]["target_key"] == "7203.T"
    assert rows[0]["disclosure_number"] == "1"
    assert "disclosure_id" not in rows[0]


def test_unprovided_values_stay_null():
    """未提供項目をゼロで埋めない。IFRSに経常利益が無いことと0を区別する。"""
    rows, _, _ = build_financial_rows([_raw()], run_id=1, schema=SCHEMA)

    assert rows[0]["revenue"] == 1000
    assert rows[0]["ordinary_income"] is None


def test_corrections_are_kept_as_separate_rows():
    """訂正開示は開示番号が別。畳まずに残す。どれが最新かは利用側が決める。"""
    rows, _, _ = build_financial_rows(
        [_raw(number="1"), _raw(number="2", Sales="1200")], run_id=1, schema=SCHEMA
    )

    deduped, dropped = deduplicate(rows)

    assert dropped == 0
    assert len(deduped) == 2


def test_same_disclosure_archived_twice_is_collapsed():
    """同じ開示日を複数回アーカイブしたぶんだけを畳む。後の取込を残す。"""
    first, _, _ = build_financial_rows([_raw(number="1")], run_id=1, schema=SCHEMA)
    second, _, _ = build_financial_rows([_raw(number="1", Sales="1500")], run_id=2, schema=SCHEMA)

    deduped, dropped = deduplicate(first + second)

    assert dropped == 1
    assert deduped[0]["ingestion_run_id"] == 2
    assert deduped[0]["revenue"] == 1500


def test_unmapped_securities_are_kept():
    """target_keyを引けなくても行は残す。jpx_codeで識別できる。"""
    rows, _, unmapped = build_financial_rows([_raw(code="25935")], run_id=1, schema=SCHEMA)

    assert unmapped == 1
    assert len(rows) == 1
    assert rows[0]["target_key"] is None
    assert rows[0]["jpx_code"] == "25935"


def test_rows_match_the_declared_schema():
    """スキーマに載らない列を作らない。Parquet書き出しが落ちる前に気づく。"""
    rows, _, _ = build_financial_rows([_raw()], run_id=1, schema=SCHEMA)

    table = pa.Table.from_pylist(rows, schema=SCHEMA)

    assert table.num_rows == 1


@pytest.mark.parametrize(("code", "expected"), [("72030", "7203.T"), ("25935", None)])
def test_target_key_mapping(code, expected):
    assert jpx_code_to_target_key(code) == expected


def test_disclosure_fields_are_not_treated_as_values():
    """開示メタデータを財務値の列と取り違えない。"""
    value_columns = {n for n in SCHEMA.names} - {f for f, _ in DISCLOSURE_FIELDS}

    assert "disclosed_date" not in value_columns
    assert "revenue" in value_columns


# `reporting_scope` は開示メタ側に置いてある。値ではなく開示の属性。
_METADATA_VALUES = {"reporting_scope"}


def _value_columns() -> set[str]:
    metadata = {name for name, _ in DISCLOSURE_FIELDS}
    return {name for name in build_schema().names if name not in metadata}


def test_schema_covers_every_column_the_api_returns():
    """APIモデルの値の列が全てParquetにある。欠けると画面の項目が空になる。

    正規化が扱う対応表は連結・非連結・共通で4つに分かれている。スキーマを組むときに
    1つでも取り落とすと、正規化は値を作っているのにParquetへ入らない。実際に
    `SHARED_*`（キャッシュフロー、配当、株式数の9列）を漏らし、37,678開示すべてで
    欠けた状態を作った。
    """
    expected = set(FinancialSummaryValues.model_fields) - _METADATA_VALUES

    assert _value_columns() == expected


def test_schema_covers_every_mapping_the_normalizer_uses():
    """正規化が作る列を取りこぼさない。対応表の追加漏れをここで止める。"""
    produced = (
        set(FINANCIAL_INT_FIELDS)
        | set(FINANCIAL_REAL_FIELDS)
        | set(NON_CONSOLIDATED_INT_FIELDS)
        | set(NON_CONSOLIDATED_REAL_FIELDS)
        | set(SHARED_INT_FIELDS)
        | set(SHARED_REAL_FIELDS)
    )

    assert produced <= _value_columns(), (
        "正規化が値を作るのにParquetへ入らない列がある"
    )


def test_cash_flow_and_dividend_columns_are_present():
    """取りこぼした実例を名前で固定する。集合の比較だけだと再発に気づきにくい。"""
    columns = _value_columns()

    for column in (
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "cash_equivalents",
        "annual_dividend_per_share",
        "forecast_annual_dividend_per_share",
        "shares_outstanding",
        "treasury_shares",
        "average_shares",
    ):
        assert column in columns, f"{column} が欠けている"
