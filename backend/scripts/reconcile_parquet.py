"""Parquet（分析層）とPostgreSQL（配信層）を突合する。

**値の完全一致は期待しない。** PostgreSQLの価格はyfinanceと移行前の`legacy_unknown`由来、
Parquetの価格はJ-Quantsの調整済み値で、取得元も調整基準日も違う。同じ日の終値が一致
しないこと自体は異常ではない。

したがってここでは2種類を分けて扱う。

- **構造の検査** … 一致しなければ変換の不具合。検出したら異常終了する
  （重複キー、OHLCの大小関係、プラン窓の外の日付、銘柄数の急減）
- **値の比較** … 取得元差を含むため、乖離の分布を報告するだけで合否にしない

「揃っているように見せる」ことではなく、**ずれの大きさと理由を説明できる状態**を作るのが
目的である。

使い方:
    python backend/scripts/reconcile_parquet.py --parquet data/parquet
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect_database

# 取得元が違えば調整基準も違うため、この程度のずれは異常ではない。
# これを超える銘柄は、列の取り違えや桁違いを疑う。
SUSPICIOUS_RELATIVE_DIFF = 0.5


@dataclass
class Finding:
    """突合で見つかった問題。`fatal` なものだけが終了コードに影響する。"""

    fatal: bool
    message: str


def parquet_glob(parquet_dir: Path) -> str:
    return str(parquet_dir / "observed" / "market_price" / "**" / "*.parquet")


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """集計クエリの単一値を取り出す。結果が無いのは想定外なので明示的に落とす。"""
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"集計結果が空です: {sql.strip()[:60]}")
    return row[0]


def check_structure(con: duckdb.DuckDBPyConnection, source: str) -> list[Finding]:
    """Parquet単体で完結する検査。取得元差に左右されない。"""
    findings: list[Finding] = []

    summary = con.execute(
        f"""
        SELECT COUNT(*), COUNT(DISTINCT COALESCE(target_key, jpx_code)),
               MIN(obs_date), MAX(obs_date)
        FROM read_parquet('{source}', hive_partitioning=true)
        """
    ).fetchone()
    if summary is None:
        raise RuntimeError("Parquetが読めません")
    rows, symbols, lo, hi = summary
    print(f"  行数 {rows:,} / 銘柄 {symbols:,} / {lo} 〜 {hi}")

    duplicates = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM (
            SELECT jpx_code, obs_date, source_key
            FROM read_parquet('{source}', hive_partitioning=true)
            GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
        )
        """,
    )
    if duplicates:
        findings.append(
            Finding(True, f"重複キー {duplicates:,}件（jpx_code × obs_date × source_key）")
        )

    # 欠損はNULLのまま許容するが、値がある行では大小関係が崩れていてはいけない。
    broken = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM read_parquet('{source}', hive_partitioning=true)
        WHERE high_price IS NOT NULL AND low_price IS NOT NULL
          AND (low_price > high_price
               OR (close_price IS NOT NULL AND (close_price > high_price OR close_price < low_price))
               OR (open_price IS NOT NULL AND (open_price > high_price OR open_price < low_price)))
        """,
    )
    if broken:
        findings.append(Finding(True, f"OHLCの大小関係が崩れた行 {broken:,}件"))

    negative = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM read_parquet('{source}', hive_partitioning=true)
        WHERE close_price < 0 OR volume < 0
        """,
    )
    if negative:
        findings.append(Finding(True, f"負の価格または出来高 {negative:,}件"))

    # 1日あたりの銘柄数が急減していたら、その日の取得が不完全だった可能性が高い。
    thin_days = scalar(
        con,
        f"""
        WITH per_day AS (
            SELECT obs_date, COUNT(*) AS n
            FROM read_parquet('{source}', hive_partitioning=true)
            GROUP BY 1
        )
        SELECT COUNT(*) FROM per_day
        WHERE n < (SELECT MEDIAN(n) FROM per_day) * 0.5
        """,
    )
    if thin_days:
        findings.append(
            Finding(False, f"銘柄数が中央値の半分未満の日 {thin_days}日（取得漏れの可能性）")
        )

    return findings


def compare_with_postgres(
    con: duckdb.DuckDBPyConnection, source: str
) -> list[Finding]:
    """PostgreSQLと重なる範囲で終値を比べ、乖離の分布を報告する。

    取得元が違うため合否にはしない。桁違いだけを疑う。
    """
    findings: list[Finding] = []
    connection = connect_database(read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT t.target_key, s.source_key, p.obs_date, p.close_price
            FROM market_price_observation p
            JOIN investment_target t USING (target_id)
            JOIN data_source s USING (source_id)
            WHERE p.close_price IS NOT NULL
            """
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        return [Finding(False, "PostgreSQLに比較できる価格がありません")]

    con.execute(
        "CREATE OR REPLACE TABLE pg_prices (target_key VARCHAR, source_key VARCHAR, "
        "obs_date DATE, close_price DOUBLE)"
    )
    con.executemany(
        "INSERT INTO pg_prices VALUES (?, ?, ?, ?)",
        [
            (r["target_key"], r["source_key"], r["obs_date"], float(r["close_price"]))
            for r in rows
        ],
    )

    matched = con.execute(
        f"""
        SELECT g.target_key, g.source_key, COUNT(*) AS n,
               MEDIAN(ABS(q.close_price - g.close_price) / g.close_price) AS median_diff,
               MAX(ABS(q.close_price - g.close_price) / g.close_price) AS max_diff
        FROM pg_prices g
        JOIN read_parquet('{source}', hive_partitioning=true) q
          ON q.target_key = g.target_key AND q.obs_date = g.obs_date
        GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).fetchall()

    if not matched:
        return [Finding(True, "PostgreSQLとParquetで重なる (銘柄, 日付) が1件もありません")]

    print("\n  終値の乖離（取得元が違うため一致は期待しない）")
    print(f"    {'銘柄':<10} {'PG側取得元':<16} {'件数':>5} {'中央値':>9} {'最大':>9}")
    for target_key, source_key, n, median_diff, max_diff in matched:
        print(
            f"    {target_key:<10} {source_key:<16} {n:>5} "
            f"{median_diff:>8.2%} {max_diff:>8.2%}"
        )
        if median_diff > SUSPICIOUS_RELATIVE_DIFF:
            findings.append(
                Finding(
                    True,
                    f"{target_key}（{source_key}）の乖離中央値が {median_diff:.0%}。"
                    "列の取り違えや桁違いを疑う",
                )
            )

    # PostgreSQLにあってParquetに無い日を数える。窓の外は対象外。
    missing = scalar(
        con,
        f"""
        SELECT COUNT(*) FROM pg_prices g
        WHERE g.obs_date BETWEEN
              (SELECT MIN(obs_date) FROM read_parquet('{source}', hive_partitioning=true))
          AND (SELECT MAX(obs_date) FROM read_parquet('{source}', hive_partitioning=true))
          AND NOT EXISTS (
              SELECT 1 FROM read_parquet('{source}', hive_partitioning=true) q
              WHERE q.target_key = g.target_key AND q.obs_date = g.obs_date
          )
        """,
    )
    if missing:
        findings.append(
            Finding(False, f"PostgreSQLにあってParquetに無い (銘柄, 日付) {missing:,}件")
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="ParquetとPostgreSQLを突合する")
    parser.add_argument("--parquet", type=Path, default=Path("data/parquet"))
    args = parser.parse_args()

    source = parquet_glob(args.parquet)
    con = duckdb.connect()

    print("=== Parquetの構造 ===")
    findings = check_structure(con, source)

    print("\n=== PostgreSQLとの比較 ===")
    findings.extend(compare_with_postgres(con, source))

    fatal = [f for f in findings if f.fatal]
    warnings = [f for f in findings if not f.fatal]

    if warnings:
        print("\n  注意（合否には影響しない）")
        for finding in warnings:
            print(f"    - {finding.message}")
    if fatal:
        print("\n  異常（変換の不具合を疑う）")
        for finding in fatal:
            print(f"    - {finding.message}")
        return 1

    print("\n構造の検査に問題はありません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
