"""Pure transformations from provider payloads to internal records."""

import hashlib
import json
from datetime import date
from typing import Any

from app.etl.models import FinancialRecord, InvestmentTargetMasterRecord, PriceRecord

PRODUCT_TYPES = {
    "014": "etf",
    "023": "etf",
    "011": "individual_stock",
    "012": "individual_stock",
    "013": "individual_stock",
    "021": "individual_stock",
    "022": "individual_stock",
    "024": "individual_stock",
}


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_flag(value: Any) -> bool | None:
    """J-Quantsの`0`/`1`フラグをbooleanへ変換する。未知値を真扱いしない。"""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text == "0":
        return False
    if text == "1":
        return True
    raise ValueError(f"unknown flag value: {value!r}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_date(value: Any) -> str:
    text = str(value).strip()
    parsed = date.fromisoformat(text)
    return parsed.isoformat()


def jpx_code_to_target_key(code: str) -> str:
    value = str(code).strip()
    if len(value) == 5 and value.endswith("0"):
        return f"{value[:4]}.T"
    return f"{value}.T"


def target_key_to_jpx_code(target_key: str) -> str | None:
    """内部の target_key からJ-Quantsの5桁Codeを導出する。

    `7203.T` → `72030`、`408A.T` → `408A0` のように、東証銘柄は末尾に `0` を補う。
    日本株以外など、対応するCodeを導出できない場合は None を返す。
    """
    value = str(target_key).strip()
    if not value.endswith(".T"):
        return None
    base = value[:-2]
    if len(base) != 4 or not base[0].isdigit():
        return None
    return f"{base}0"


def normalize_jquants_master(row: dict[str, Any]) -> InvestmentTargetMasterRecord:
    code = str(row["Code"]).strip()
    return InvestmentTargetMasterRecord(
        jpx_code=code,
        target_key=jpx_code_to_target_key(code),
        target_name=str(row["CoName"]).strip(),
        target_type=PRODUCT_TYPES.get(str(row.get("ProdCat", "")).strip()),
        market=(str(row["Mkt"]).strip() if row.get("Mkt") not in (None, "") else None),
    )


def normalize_jquants_price(row: dict[str, Any]) -> PriceRecord:
    return PriceRecord(
        jpx_code=str(row["Code"]).strip(),
        obs_date=normalize_date(row["Date"]),
        raw_open_price=_optional_float(row.get("O")),
        raw_high_price=_optional_float(row.get("H")),
        raw_low_price=_optional_float(row.get("L")),
        # PBRのような1株指標では、株式分割前後で価格とBPSの株数基準を合わせるために
        # 未調整終値と日次調整係数が必要になる。リターン表示は従来どおりAdj*を使う。
        raw_close_price=_optional_float(row.get("C")),
        raw_volume=_optional_float(row.get("Vo")),
        turnover_value=_optional_float(row.get("Va")),
        open_price=_optional_float(row.get("AdjO")),
        high_price=_optional_float(row.get("AdjH")),
        low_price=_optional_float(row.get("AdjL")),
        close_price=_optional_float(row.get("AdjC")),
        volume=_optional_float(row.get("AdjVo")),
        adjustment_factor=_optional_float(row.get("AdjFactor")),
        market_cap_million_yen=_optional_float(row.get("MktCap")),
        ex_rights_type=_optional_string(row.get("ExRT")),
        upper_limit_flag=_optional_flag(row.get("UL")),
        lower_limit_flag=_optional_flag(row.get("LL")),
    )


# --- 財務サマリー（J-Quants /fins/summary） ---------------------------------
#
# 対応の正本は docs/traceability/jquants-financial-summary.md。
# 項目名は2026-09-14に実レスポンス（111項目）で確認済み。

# 連結開示で採用する項目。
FINANCIAL_INT_FIELDS: dict[str, str] = {
    "revenue": "Sales",
    "operating_income": "OP",
    "ordinary_income": "OdP",
    "net_income": "NP",
    "total_assets": "TA",
    "equity": "Eq",
    "forecast_revenue": "FSales",
    "forecast_operating_income": "FOP",
    "forecast_ordinary_income": "FOdP",
    "forecast_net_income": "FNP",
    "next_forecast_revenue": "NxFSales",
    "next_forecast_operating_income": "NxFOP",
    "next_forecast_ordinary_income": "NxFOdP",
    # 翌期予想の純利益だけ末尾が小文字（NxFNP ではない）。
    "next_forecast_net_income": "NxFNp",
}

FINANCIAL_REAL_FIELDS: dict[str, str] = {
    "eps": "EPS",
    "bps": "BPS",
    "forecast_eps": "FEPS",
    "next_forecast_eps": "NxFEPS",
}

# 非連結開示で置き換える項目。CF・株式数は単体/連結の区別が無いため共通。
NON_CONSOLIDATED_INT_FIELDS: dict[str, str] = {
    "revenue": "NCSales",
    "operating_income": "NCOP",
    "ordinary_income": "NCOdP",
    "net_income": "NCNP",
    "total_assets": "NCTA",
    "equity": "NCEq",
    "forecast_revenue": "FNCSales",
    "forecast_operating_income": "FNCOP",
    "forecast_ordinary_income": "FNCOdP",
    "forecast_net_income": "FNCNP",
    "next_forecast_revenue": "NxFNCSales",
    "next_forecast_operating_income": "NxFNCOP",
    "next_forecast_ordinary_income": "NxFNCOdP",
    "next_forecast_net_income": "NxFNCNP",
}

NON_CONSOLIDATED_REAL_FIELDS: dict[str, str] = {
    "eps": "NCEPS",
    "bps": "NCBPS",
    "forecast_eps": "FNCEPS",
    "next_forecast_eps": "NxFNCEPS",
}

# 連結・非連結に関わらず共通の項目。
SHARED_INT_FIELDS: dict[str, str] = {
    "operating_cash_flow": "CFO",
    "investing_cash_flow": "CFI",
    "financing_cash_flow": "CFF",
    "cash_equivalents": "CashEq",
    "shares_outstanding": "ShOutFY",
    "treasury_shares": "TrShFY",
    "average_shares": "AvgSh",
}

# DivAnn / FDivAnn は年間1株配当。DivTotalAnn は配当金の総額なので使わない。
SHARED_REAL_FIELDS: dict[str, str] = {
    "annual_dividend_per_share": "DivAnn",
    "forecast_annual_dividend_per_share": "FDivAnn",
}


def parse_document_type(document_type: str) -> tuple[str | None, str | None]:
    """`DocType` から会計基準と連結範囲を取り出す。

    決算短信の `DocType` は `1QFinancialStatements_Consolidated_IFRS` の形式で、
    連結範囲と会計基準を含む。`EarnForecastRevision` のように含まない種別もあるため、
    その場合は両方 None を返す。
    """
    parts = document_type.split("_")
    if len(parts) < 3 or "FinancialStatements" not in parts[0]:
        return None, None
    scope = "non_consolidated" if parts[1] == "NonConsolidated" else "consolidated"
    return parts[2], scope


def optional_text(value: Any) -> str | None:
    """空文字・欠損を NULL として扱う。ゼロ補完はしない。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    """金額・株式数を整数へ変換する。小数表記でも桁落ちさせずに丸める。"""
    text = optional_text(value)
    if text is None:
        return None
    return round(float(text))


def _optional_real(value: Any) -> float | None:
    """EPS・BPS・一株配当を実数へ変換する。"""
    text = optional_text(value)
    if text is None:
        return None
    return float(text)


def _optional_date(value: Any) -> str | None:
    """ISO日付へ統一する。空文字は NULL。"""
    text = optional_text(value)
    return normalize_date(text) if text else None


def source_record_hash(row: dict[str, Any]) -> str:
    """原レコードの改変検知用ハッシュ。キー順に依存しない形で算出する。"""
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_jquants_financial(row: dict[str, Any]) -> FinancialRecord:
    """`/fins/summary` の1レコードを内部形式へ変換する。

    連結範囲は `DocType` から判定し、非連結の開示では `NC*` 系の項目を採用する。
    範囲を判定できない開示（業績予想の修正など）は連結として扱う。
    """
    document_type = str(row["DocType"]).strip()
    accounting_standard, scope = parse_document_type(document_type)
    reporting_scope = scope or "consolidated"

    if reporting_scope == "non_consolidated":
        int_fields = {**NON_CONSOLIDATED_INT_FIELDS, **SHARED_INT_FIELDS}
        real_fields = {**NON_CONSOLIDATED_REAL_FIELDS, **SHARED_REAL_FIELDS}
    else:
        int_fields = {**FINANCIAL_INT_FIELDS, **SHARED_INT_FIELDS}
        real_fields = {**FINANCIAL_REAL_FIELDS, **SHARED_REAL_FIELDS}

    values: dict[str, int | float] = {}
    for column, key in int_fields.items():
        as_int = _optional_int(row.get(key))
        if as_int is not None:
            values[column] = as_int
    for column, key in real_fields.items():
        as_real = _optional_real(row.get(key))
        if as_real is not None:
            values[column] = as_real

    return FinancialRecord(
        jpx_code=str(row["Code"]).strip(),
        disclosure_number=str(row["DiscNo"]).strip(),
        disclosed_date=normalize_date(row["DiscDate"]),
        disclosed_time=optional_text(row.get("DiscTime")),
        document_type=document_type,
        fiscal_period_type=optional_text(row.get("CurPerType")),
        period_start=_optional_date(row.get("CurPerSt")),
        period_end=_optional_date(row.get("CurPerEn")),
        fiscal_year_start=_optional_date(row.get("CurFYSt")),
        fiscal_year_end=_optional_date(row.get("CurFYEn")),
        accounting_standard=accounting_standard,
        reporting_scope=reporting_scope,
        values=values,
        source_record_hash=source_record_hash(row),
    )
