"""財務の正規化（J-Quants /fins/summary）を検証する。

以前はPostgreSQLへ保存してから読み返していたが、財務をPostgreSQLへ入れなくなったため、
正規化した結果を直接見る。検証しているのは同じ規則で、`build_financial_parquet.py` が
この正規化を通してParquetを組み立てる。

項目名の癖（`NxFNp` だけ末尾が小文字、配当は総額ではなく1株当たり、非連結では `NC` 系を
採用）は実レスポンスで確認したものを固定している。
"""

import pytest

from app.etl.normalizers import normalize_jquants_financial, parse_document_type

BASE_ROW = {
    "Code": "86970",
    "DiscNo": "20260510001",
    "DiscDate": "2026-05-10",
    "DiscTime": "15:00:00",
    "DocType": "FYFinancialStatements_Consolidated_IFRS",
    "CurPerType": "FY",
    "CurPerSt": "2025-04-01",
    "CurPerEn": "2026-03-31",
    "CurFYSt": "2025-04-01",
    "CurFYEn": "2026-03-31",
    "Sales": "142000000000",
    "OP": "78000000000",
    "OdP": "79000000000",
    "NP": "52000000000",
    "EPS": "98.45",
    "TA": "1200000000000",
    "Eq": "300000000000",
    "BPS": "560.25",
    "CFO": "60000000000",
    "CFI": "-20000000000",
    "CFF": "-15000000000",
    "CashEq": "180000000000",
    "FSales": "150000000000",
    "FOP": "80000000000",
    "FNP": "55000000000",
    "ShOutFY": "540000000",
    "TrShFY": "12000000",
    "AvgSh": "528000000",
}


def test_amounts_are_integers_and_per_share_values_are_real():
    """金額は整数、EPS・BPSは実数として取り出す。"""
    record = normalize_jquants_financial(BASE_ROW)

    assert record.disclosure_number == "20260510001"
    assert record.disclosed_date == "2026-05-10"
    assert record.reporting_scope == "consolidated"
    assert record.values["revenue"] == 142000000000
    assert isinstance(record.values["revenue"], int)
    assert record.values["eps"] == pytest.approx(98.45)


def test_missing_values_are_absent_instead_of_zero():
    """空文字・欠損は値として持たない。ゼロで補完しない。"""
    row = {**BASE_ROW, "CFO": "", "CashEq": None}
    del row["TrShFY"]

    values = normalize_jquants_financial(row).values

    for column in ("operating_cash_flow", "cash_equivalents", "treasury_shares"):
        assert column not in values


def test_ifrs_disclosure_without_ordinary_income_is_accepted():
    """IFRS等で経常利益が無い開示も、他の項目は取り出す。"""
    values = normalize_jquants_financial({**BASE_ROW, "OdP": ""}).values

    assert "ordinary_income" not in values
    assert values["operating_income"] == 78000000000


def test_correction_keeps_its_own_disclosure_number():
    """訂正開示は開示番号が別なので、元の開示と区別できる。

    別レコードとして残す責務はParquetの組み立て側にある
    （`test_build_financial_parquet.py`）。
    """
    correction = {**BASE_ROW, "DiscNo": "20260612001", "Sales": "141000000000"}

    original = normalize_jquants_financial(BASE_ROW)
    revised = normalize_jquants_financial(correction)

    assert original.disclosure_number != revised.disclosure_number
    assert original.values["revenue"] != revised.values["revenue"]


def test_source_record_hash_detects_content_change():
    """原レコードが変われば source_record_hash も変わる。"""
    first = normalize_jquants_financial(BASE_ROW).source_record_hash
    same = normalize_jquants_financial(dict(reversed(list(BASE_ROW.items())))).source_record_hash
    changed = normalize_jquants_financial({**BASE_ROW, "Sales": "1"}).source_record_hash

    assert first == same  # キー順には依存しない
    assert first != changed


@pytest.mark.parametrize(
    "document_type, expected",
    [
        ("1QFinancialStatements_Consolidated_IFRS", ("IFRS", "consolidated")),
        ("FYFinancialStatements_Consolidated_JP", ("JP", "consolidated")),
        ("FYFinancialStatements_NonConsolidated_JP", ("JP", "non_consolidated")),
        # 業績予想の修正には会計基準も連結範囲も含まれない
        ("EarnForecastRevision", (None, None)),
    ],
)
def test_document_type_carries_standard_and_scope(document_type, expected):
    """会計基準と連結範囲は専用項目ではなく DocType から判定する。"""
    assert parse_document_type(document_type) == expected


def test_non_consolidated_disclosure_uses_nc_fields():
    """非連結の開示では NC 系の項目を採用する。"""
    record = normalize_jquants_financial({
        **BASE_ROW,
        "DocType": "FYFinancialStatements_NonConsolidated_JP",
        "NCSales": "62933000000",
        "NCEPS": "53.01",
    })

    assert record.accounting_standard == "JP"
    assert record.reporting_scope == "non_consolidated"
    # 連結の Sales / EPS ではなく NC 側が入る
    assert record.values["revenue"] == 62933000000
    assert record.values["eps"] == pytest.approx(53.01)


def test_annual_dividend_uses_per_share_not_total():
    """配当は1株当たりの `DivAnn` を採用し、総額の `DivTotalAnn` は使わない。"""
    values = normalize_jquants_financial(
        {**BASE_ROW, "DivAnn": "61.0", "DivTotalAnn": "62938000000"}
    ).values

    assert values["annual_dividend_per_share"] == pytest.approx(61.0)


def test_next_period_forecast_field_names():
    """翌期予想の純利益だけ項目名の末尾が小文字（`NxFNp`）である。"""
    values = normalize_jquants_financial(
        {**BASE_ROW, "NxFSales": "205000000000", "NxFNp": "77500000000"}
    ).values

    assert values["next_forecast_revenue"] == 205000000000
    assert values["next_forecast_net_income"] == 77500000000


def test_fetcher_requires_code_or_date():
    """クライアントは code も date も無い呼び出しを拒否する。"""
    from app.etl.fetchers.jquants import JQuantsClient, JQuantsError

    client = JQuantsClient("secret", opener=lambda *_a, **_k: None)
    with pytest.raises(JQuantsError, match="requires code or date"):
        client.financial_summaries()
