"""EDINET DBを過去財務の取得元として採用できるか実測する。

このスクリプトはResearch専用であり、PostgreSQL・GCS・公開APIへ書き込まない。
業種から20〜50社を選び、年次財務とセグメントをローカルへキャッシュしたうえで、
年度連続性、科目充足率、EDINET原本への来歴、J-Quantsとの重複値を測る。

無料枠は100リクエスト/日である。既定の20社では企業一覧1回、financials 20回、
segments 20回の約41回を使う。キャッシュ済みレスポンスは再取得しない。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "research" / "outputs" / "edinetdb_source_qualification"
BASE_URL = "https://edinetdb.jp/v1"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    aliases: tuple[str, ...]
    gate_pct: float | None = None


FIELDS = (
    FieldSpec("revenue", ("revenue", "net_sales", "sales"), 90.0),
    FieldSpec("cost_of_sales", ("cost_of_sales", "cogs", "cost_of_revenue"), 70.0),
    FieldSpec("gross_profit", ("gross_profit",)),
    FieldSpec("sga", ("sga", "selling_general_administrative", "operating_expenses"), 70.0),
    FieldSpec("operating_income", ("operating_income", "operating_profit"), 90.0),
    FieldSpec("inventories", ("inventories", "inventory")),
    FieldSpec("trade_receivables", ("trade_receivables", "accounts_receivable")),
    FieldSpec("operating_cash_flow", ("cf_operating", "operating_cash_flow")),
)

TRACE_FIELDS = {
    "doc_id": ("doc_id", "docID"),
    "submit_date": ("submit_date_time", "submit_date", "submitted_at"),
    "filing_url": ("edinet_filing_url", "edinetFilingUrl"),
}


def first_value(mapping: dict[str, Any], aliases: Iterable[str]) -> Any:
    """別名候補のうち、最初の非NULL値を返す。"""
    for key in aliases:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    """EDINET DBのresponse envelope差を吸収して行配列を返す。"""
    value = payload
    if isinstance(value, dict):
        value = value.get("data", value)
    if isinstance(value, dict):
        for key in ("companies", "financials", "segments", "items", "results"):
            child = value.get(key)
            if isinstance(child, list):
                value = child
                break
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def company_edinet_code(company: dict[str, Any]) -> str | None:
    value = first_value(company, ("edinet_code", "edinetCode", "code"))
    if value is None:
        return None
    code = str(value).strip().upper()
    return code if code.startswith("E") else None


def company_security_code(company: dict[str, Any]) -> str | None:
    value = first_value(
        company,
        (
            "securities_code",
            "security_code",
            "sec_code",
            "secCode",
            "ticker",
            "listing_code",
        ),
    )
    if value is None:
        return None
    code = str(value).strip().upper().removesuffix(".T").removesuffix(".0")
    if len(code) == 4 and code.isalnum():
        return f"{code}0"
    if len(code) == 5 and code.isalnum():
        return code
    return None


def fiscal_year(row: dict[str, Any]) -> int | None:
    value = first_value(row, ("fiscal_year", "fiscalYear", "year"))
    if value is None:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None


def longest_consecutive_run(years: Iterable[int]) -> int:
    unique = sorted(set(years))
    longest = current = 0
    previous: int | None = None
    for year in unique:
        current = current + 1 if previous is not None and year == previous + 1 else 1
        longest = max(longest, current)
        previous = year
    return longest


class EdinetDbClient:
    """API responseをローカルへ保存し、無料枠を無駄にしないclient。"""

    def __init__(self, api_key: str, cache_dir: Path, *, refresh: bool = False) -> None:
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.request_count = 0
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        query = urllib.parse.urlencode(params or {})
        relative_url = f"{path}?{query}" if query else path
        digest = hashlib.sha256(relative_url.encode()).hexdigest()[:12]
        label = path.strip("/").replace("/", "-") or "root"
        cache_path = self.cache_dir / f"{label}-{digest}.json"
        if cache_path.exists() and not self.refresh:
            return json.loads(cache_path.read_text(encoding="utf-8"))["payload"]

        request = urllib.request.Request(f"{BASE_URL}{relative_url}")
        request.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(
                request,
                timeout=60,
                context=self.ssl_context,
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code == 404 and allow_not_found:
                payload = {"data": [], "qualification_note": "endpoint_not_found"}
            else:
                raise RuntimeError(
                    f"EDINET DB API HTTP {error.code}: {path}: {body[:500]}"
                ) from error
        self.request_count += 1
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "request_path": relative_url,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return payload


def summarize_company(
    company: dict[str, Any],
    financial_rows: list[dict[str, Any]],
    segment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    years = [year for row in financial_rows if (year := fiscal_year(row)) is not None]
    result: dict[str, Any] = {
        "edinet_code": company_edinet_code(company),
        "jpx_code": company_security_code(company),
        "company_name": first_value(company, ("name", "company_name", "companyName")),
        "industry": first_value(company, ("industry", "industry_name", "industryName")),
        "annual_rows": len(financial_rows),
        "min_fiscal_year": min(years) if years else None,
        "max_fiscal_year": max(years) if years else None,
        "distinct_fiscal_years": len(set(years)),
        "longest_consecutive_years": longest_consecutive_run(years),
        "duplicate_fiscal_year_rows": len(years) - len(set(years)),
        "has_three_consecutive_years": longest_consecutive_run(years) >= 3,
        "segment_rows": len(segment_rows),
        "has_segments": bool(segment_rows),
    }
    for field in FIELDS:
        present = sum(first_value(row, field.aliases) is not None for row in financial_rows)
        result[f"{field.key}_present_rows"] = present
        result[f"{field.key}_coverage_pct"] = (
            round(present / len(financial_rows) * 100, 2) if financial_rows else 0.0
        )
    for label, aliases in TRACE_FIELDS.items():
        present = sum(first_value(row, aliases) is not None for row in financial_rows)
        result[f"{label}_coverage_pct"] = (
            round(present / len(financial_rows) * 100, 2) if financial_rows else 0.0
        )
    standards = sorted(
        {
            str(value)
            for row in financial_rows
            if (value := first_value(row, ("accounting_standard", "accountingStandard")))
        }
    )
    result["accounting_standards"] = "|".join(standards)
    return result


def aggregate_field_coverage(
    financials: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    all_rows = [row for rows in financials.values() for row in rows]
    output: list[dict[str, Any]] = []
    for field in FIELDS:
        present = sum(first_value(row, field.aliases) is not None for row in all_rows)
        known_null_reason = sum(
            isinstance(row.get("null_reasons"), dict)
            and any(alias in row["null_reasons"] for alias in field.aliases)
            for row in all_rows
        )
        coverage = round(present / len(all_rows) * 100, 2) if all_rows else 0.0
        output.append(
            {
                "field": field.key,
                "present_rows": present,
                "total_rows": len(all_rows),
                "coverage_pct": coverage,
                "known_null_reason_rows": known_null_reason,
                "unknown_null_rows": len(all_rows) - present - known_null_reason,
                "gate_pct": field.gate_pct,
                "gate_passed": field.gate_pct is None or coverage >= field.gate_pct,
            }
        )
    return output


def select_recent_years(
    financials: dict[str, list[dict[str, Any]]], years: int
) -> dict[str, list[dict[str, Any]]]:
    """企業ごとに直近N年度を採用ゲートの評価対象として選ぶ。"""
    selected: dict[str, list[dict[str, Any]]] = {}
    for code, rows in financials.items():
        dated_rows = [(year, row) for row in rows if (year := fiscal_year(row)) is not None]
        selected[code] = [
            row for _, row in sorted(dated_rows, key=lambda item: item[0], reverse=True)[:years]
        ]
    return selected


def summarize_quality_dimensions(
    financials: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """連結区分・会計基準・来歴の分布を集計する。"""
    rows = [row for company_rows in financials.values() for row in company_rows]
    dimensions = {
        "basis": ("basis",),
        "accounting_standard": ("accounting_standard", "accountingStandard"),
    }
    output: list[dict[str, Any]] = []
    for dimension, aliases in dimensions.items():
        counts: dict[str, int] = {}
        for row in rows:
            value = first_value(row, aliases)
            label = str(value) if value is not None else "unknown"
            counts[label] = counts.get(label, 0) + 1
        output.extend(
            {
                "dimension": dimension,
                "value": value,
                "row_count": count,
                "coverage_pct": round(count / len(rows) * 100, 2) if rows else 0.0,
            }
            for value, count in sorted(counts.items())
        )

    for label, aliases in TRACE_FIELDS.items():
        present = sum(first_value(row, aliases) is not None for row in rows)
        output.append(
            {
                "dimension": "traceability",
                "value": label,
                "row_count": present,
                "coverage_pct": round(present / len(rows) * 100, 2) if rows else 0.0,
            }
        )
    return output


def build_missing_field_details(
    financials: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """必須科目の欠損を企業・年度・品質属性単位で返す。"""
    output: list[dict[str, Any]] = []
    required_fields = [field for field in FIELDS if field.gate_pct is not None]
    for edinet_code, rows in financials.items():
        for row in rows:
            for field in required_fields:
                if first_value(row, field.aliases) is None:
                    null_reasons = row.get("null_reasons", {})
                    output.append(
                        {
                            "edinet_code": edinet_code,
                            "fiscal_year": fiscal_year(row),
                            "field": field.key,
                            "basis": row.get("basis"),
                            "accounting_standard": first_value(
                                row, ("accounting_standard", "accountingStandard")
                            ),
                            "doc_id": first_value(row, TRACE_FIELDS["doc_id"]),
                            "submit_date": first_value(row, TRACE_FIELDS["submit_date"]),
                            "null_reason": first_value(
                                null_reasons if isinstance(null_reasons, dict) else {},
                                field.aliases,
                            ),
                        }
                    )
    return output


def reconciliation_exceptions(
    reconciliation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """比較可能だが一致しない値を、差異率付きで抽出する。"""
    output: list[dict[str, Any]] = []
    for row in reconciliation_rows:
        if row["comparison_status"] != "compared" or row["exact_match"]:
            continue
        denominator = float(row["jquants_value"])
        output.append(
            {
                **row,
                "difference_pct": (
                    round(float(row["difference"]) / denominator * 100, 4)
                    if denominator
                    else None
                ),
                "review_status": "requires_semantic_or_source_review",
            }
        )
    return output


def evaluate_gate(
    companies: list[dict[str, Any]], field_coverage: list[dict[str, Any]]
) -> dict[str, Any]:
    continuity_pct = (
        round(
            sum(bool(row["has_three_consecutive_years"]) for row in companies)
            / len(companies)
            * 100,
            2,
        )
        if companies
        else 0.0
    )
    failed_fields = [
        row["field"]
        for row in field_coverage
        if row["gate_pct"] is not None and not row["gate_passed"]
    ]
    return {
        "sample_companies": len(companies),
        "companies_with_three_consecutive_years_pct": continuity_pct,
        "continuity_gate_pct": 80.0,
        "continuity_gate_passed": continuity_pct >= 80.0,
        "failed_required_fields": failed_fields,
        "field_gate_passed": not failed_fields,
        "adopt_for_backfill": continuity_pct >= 80.0 and not failed_fields,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_jquants_rows(parquet_root: Path) -> list[dict[str, Any]]:
    """J-Quantsの通期実績を、銘柄×年度ごとに最新開示へ畳んで読む。"""
    try:
        import duckdb
    except ImportError as error:  # pragma: no cover - requirementsで導入される
        raise RuntimeError("J-Quants照合にはduckdbが必要です") from error

    pattern = parquet_root / "observed" / "financial_summary" / "year=*" / "*.parquet"
    query = """
        SELECT jpx_code,
               EXTRACT(year FROM fiscal_year_end)::INTEGER AS fiscal_year,
               revenue,
               operating_income,
               disclosed_date,
               disclosure_number
        FROM read_parquet(?, hive_partitioning = true)
        WHERE fiscal_period_type = 'FY'
          AND fiscal_year_end IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY jpx_code, EXTRACT(year FROM fiscal_year_end)
            ORDER BY disclosed_date DESC, disclosure_number DESC
        ) = 1
    """
    connection = duckdb.connect()
    try:
        result = connection.execute(query, [str(pattern)]).fetchdf()
    finally:
        connection.close()
    return result.to_dict("records")


def reconcile_jquants(
    companies: list[dict[str, Any]],
    financials: dict[str, list[dict[str, Any]]],
    jquants_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    company_by_edinet = {
        company_edinet_code(company): company for company in companies if company_edinet_code(company)
    }
    jq_index = {
        (str(row["jpx_code"]), int(row["fiscal_year"])): row
        for row in jquants_rows
        if row.get("jpx_code") and row.get("fiscal_year") is not None
    }
    output: list[dict[str, Any]] = []
    for edinet_code, rows in financials.items():
        company = company_by_edinet.get(edinet_code, {})
        jpx_code = company_security_code(company)
        if not jpx_code:
            continue
        for row in rows:
            year = fiscal_year(row)
            jq = jq_index.get((jpx_code, year)) if year is not None else None
            for field in ("revenue", "operating_income"):
                edinet_value = first_value(row, next(f.aliases for f in FIELDS if f.key == field))
                jq_value = jq.get(field) if jq else None
                difference = (
                    float(edinet_value) - float(jq_value)
                    if edinet_value is not None and jq_value is not None
                    else None
                )
                output.append(
                    {
                        "edinet_code": edinet_code,
                        "jpx_code": jpx_code,
                        "fiscal_year": year,
                        "field": field,
                        "edinet_value": edinet_value,
                        "jquants_value": jq_value,
                        "difference": difference,
                        "exact_match": difference == 0 if difference is not None else None,
                        "comparison_status": "compared" if difference is not None else "unavailable",
                    }
                )
    return output


def write_markdown_report(
    path: Path,
    gate: dict[str, Any],
    field_coverage: list[dict[str, Any]],
    *,
    industry: str,
    request_count: int,
    reconciliation_rows: list[dict[str, Any]],
    qualification_years: int,
    quality_dimensions: list[dict[str, Any]],
    missing_details: list[dict[str, Any]],
    reconciliation_exception_rows: list[dict[str, Any]],
) -> None:
    compared = [row for row in reconciliation_rows if row["comparison_status"] == "compared"]
    exact = [row for row in compared if row["exact_match"]]
    lines = [
        "# EDINET DB Source Qualification Report",
        "",
        f"- 生成日時: {datetime.now(UTC).isoformat()}",
        f"- 対象業種: `{industry}`",
        f"- 対象企業数: {gate['sample_companies']}",
        f"- 科目充足率の評価期間: 企業ごとの直近{qualification_years}年度",
        f"- 今回のAPIリクエスト数（キャッシュ除外）: {request_count}",
        f"- backfill採用判定: **{'PASS' if gate['adopt_for_backfill'] else 'FAIL'}**",
        "",
        "## 採用ゲート",
        "",
        "| 項目 | 実測 | 基準 | 判定 |",
        "|---|---:|---:|---|",
        (
            "| 3年以上連続する企業 | "
            f"{gate['companies_with_three_consecutive_years_pct']:.2f}% | 80% | "
            f"{'PASS' if gate['continuity_gate_passed'] else 'FAIL'} |"
        ),
    ]
    for row in field_coverage:
        if row["gate_pct"] is None:
            continue
        lines.append(
            f"| {row['field']}充足率 | {row['coverage_pct']:.2f}% | "
            f"{row['gate_pct']:.0f}% | {'PASS' if row['gate_passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## 品質属性（採用判定期間）",
            "",
        ]
    )
    for row in quality_dimensions:
        lines.append(
            f"- {row['dimension']} / {row['value']}: "
            f"{row['row_count']}行 ({row['coverage_pct']:.2f}%)"
        )
    lines.extend(
        [
            "",
            f"必須科目の欠損は{len(missing_details)}セル。詳細は`missing_field_details.csv`。",
            "Rawでは削除せず、Curated層で連結区分・来歴・欠損理由を品質フラグとして扱う。",
            "",
            "## J-Quants照合",
            "",
            (
                f"比較可能 {len(compared)}件、完全一致 {len(exact)}件。"
                if reconciliation_rows
                else "`--jquants-parquet`未指定のため未実施。"
            ),
            f"不一致は{len(reconciliation_exception_rows)}件。詳細は`reconciliation_exceptions.csv`。",
            "金額の不一致を直ちに誤りとせず、売上高／営業収益等の概念差と原本採択差を確認する。",
            "",
            "## 制約",
            "",
            "- 本調査は第三者提供のEDINET DB APIを評価するもので、金融庁EDINET原本そのものの完全性評価ではない。",
            "- `/financials`は年度ごとの採択値を返すため、訂正前後の全versionをこの結果だけでは検証できない。",
            "- NULLは0として扱わない。業種・会計基準上の非該当と抽出失敗は、別途`null_reasons`で確認する。",
            "- APIレスポンスと出力CSVは利用条件上、公開リポジトリへ含めない。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--industry", default="retail", help="EDINET DBの業種slug")
    parser.add_argument("--sample-size", type=int, default=20, choices=range(20, 51))
    parser.add_argument(
        "--qualification-years",
        type=int,
        default=10,
        choices=range(3, 16),
        help="科目充足率の採用ゲートに使う企業ごとの直近年度数",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--refresh", action="store_true", help="ローカルキャッシュを使わず再取得")
    parser.add_argument(
        "--jquants-parquet",
        type=Path,
        help="J-Quants照合に使うParquet lakeのルート（例: data/parquet）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("EDINETDB_API_KEY")
    if not api_key:
        print(
            "EDINETDB_API_KEYが未設定です。EDINET DBの開発者画面で取得し、"
            "環境変数へ設定してください。",
            file=sys.stderr,
        )
        return 1

    client = EdinetDbClient(api_key, args.output_dir / "raw", refresh=args.refresh)
    companies_payload = client.get(
        "/companies",
        {
            "industry": args.industry,
            "sort": "revenue",
            "order": "desc",
            "page": 1,
            "per_page": args.sample_size,
        },
    )
    companies = [
        company for company in extract_rows(companies_payload) if company_edinet_code(company)
    ][: args.sample_size]
    if len(companies) < args.sample_size:
        raise RuntimeError(
            f"対象企業が{len(companies)}社しか取得できませんでした（要求: {args.sample_size}社）"
        )

    financials: dict[str, list[dict[str, Any]]] = {}
    company_rows: list[dict[str, Any]] = []
    for index, company in enumerate(companies, start=1):
        code = company_edinet_code(company)
        assert code is not None
        print(f"[{index}/{len(companies)}] {code} を確認")
        financial_rows = extract_rows(
            client.get(
                f"/companies/{code}/financials",
                {
                    "period": "annual",
                    "include_nulls": "true",
                    "include_null_reasons": "true",
                },
                allow_not_found=True,
            )
        )
        segment_rows = extract_rows(
            client.get(f"/companies/{code}/segments", allow_not_found=True)
        )
        financials[code] = financial_rows
        company_rows.append(summarize_company(company, financial_rows, segment_rows))

    qualification_financials = select_recent_years(financials, args.qualification_years)
    field_coverage = aggregate_field_coverage(qualification_financials)
    full_history_field_coverage = aggregate_field_coverage(financials)
    quality_dimensions = summarize_quality_dimensions(qualification_financials)
    missing_details = build_missing_field_details(qualification_financials)
    gate = evaluate_gate(company_rows, field_coverage)
    reconciliation_rows: list[dict[str, Any]] = []
    if args.jquants_parquet:
        reconciliation_rows = reconcile_jquants(
            companies, financials, load_jquants_rows(args.jquants_parquet)
        )
    reconciliation_exception_rows = reconciliation_exceptions(reconciliation_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "company_coverage.csv", company_rows)
    write_csv(args.output_dir / "field_coverage.csv", field_coverage)
    write_csv(
        args.output_dir / "full_history_field_coverage.csv",
        full_history_field_coverage,
    )
    write_csv(args.output_dir / "jquants_reconciliation.csv", reconciliation_rows)
    write_csv(args.output_dir / "quality_dimensions.csv", quality_dimensions)
    write_csv(args.output_dir / "missing_field_details.csv", missing_details)
    write_csv(
        args.output_dir / "reconciliation_exceptions.csv",
        reconciliation_exception_rows,
    )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "industry": args.industry,
        "request_count": client.request_count,
        "qualification_years": args.qualification_years,
        "gate": gate,
        "quality_dimensions": quality_dimensions,
        "missing_required_cells": len(missing_details),
        "reconciliation_exceptions": len(reconciliation_exception_rows),
        "known_limitations": [
            "financials endpoint does not expose every correction version",
            "null does not mean zero",
            "third-party API coverage is not equivalent to FSA source completeness",
        ],
    }
    (args.output_dir / "qualification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown_report(
        args.output_dir / "report.md",
        gate,
        field_coverage,
        industry=args.industry,
        request_count=client.request_count,
        reconciliation_rows=reconciliation_rows,
        qualification_years=args.qualification_years,
        quality_dimensions=quality_dimensions,
        missing_details=missing_details,
        reconciliation_exception_rows=reconciliation_exception_rows,
    )
    print(f"結果: {args.output_dir}")
    print(f"backfill採用判定: {'PASS' if gate['adopt_for_backfill'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
