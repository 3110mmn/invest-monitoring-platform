"""分析層のParquetが、変換の不具合を持っていないかを検査する。

**比較相手はもう無い。** 以前はPostgreSQLの `market_price_observation` と終値を
突き合わせていたが、価格をParquetからしか読まなくなり、そのテーブルは削除した。
移行時の突合では終値の乖離が中央値0.00%で、構造検査も通っていた。

残したのは、Parquet自身の健全性を見る検査である。比較相手が無くても、次のような
変換の不具合は検出できる。

- 同じ (銘柄, 日付, 取得元) が二重にある
- 高値 < 安値、終値が高安の外側にあるなど、四本値の大小関係が壊れている
- 価格や出来高が負

異常があれば終了コード1で止める。取得漏れの可能性など、判断が要るものは警告として
報告するだけにして合否に混ぜない。

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Parquetの構造を検査する")
    parser.add_argument("--parquet", type=Path, default=Path("data/parquet"))
    args = parser.parse_args()

    source = parquet_glob(args.parquet)
    con = duckdb.connect()

    print("=== Parquetの構造 ===")
    findings = check_structure(con, source)

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
