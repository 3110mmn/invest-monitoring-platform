"""dbt docsの成果物に実データが含まれていないことを検査する。

**公開して良い前提が崩れていないかを、publishのたびに確かめる。** dbt docsが含むのは
モデルの依存グラフ、列定義、テスト、コンパイル済みSQLであり、観測値そのものではない。
この前提が成り立つ限り、J-Quantsの再配布禁止（取得データの第三者配信）には当たらない。

ただし前提は自動では守られない。次のような変更で、値が成果物へ混ざりうる。

- `dbt docs generate` に行数統計が付く設定を足す（`catalog.json` の `stats`）
- モデルへ銘柄コードや金額をリテラルで書く
- `description` に実際の観測値を例として書く

検査するのは3点。

1. `catalog.json` に行数・バイト数等の統計が入っていないか
2. compiled SQLに銘柄コードのリテラルが埋まっていないか
3. 認証情報・接続文字列が混ざっていないか

GCPプロジェクトIDは検出対象にしない。`docs/architecture.md`で公開済みであり、
秘密ではなくIAMで保護する対象のため。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 行数やバイト数を示す統計キー。これが値を持つと、銘柄ごとの規模が推測できる。
FORBIDDEN_STAT_KEYS = ("num_rows", "row_count", "num_bytes", "size", "bytes")

# 日本株の証券コードらしいリテラル。'72030' や '7203.T' を検出する。
TICKER_LITERAL = re.compile(r"'\d{4,5}\.?T?'")

# 認証情報らしい文字列。
SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "秘密鍵"),
    (re.compile(r"\.iam\.gserviceaccount\.com"), "サービスアカウント"),
    (re.compile(r"postgres(?:ql)?://[^\s\"']+"), "PostgreSQL接続文字列"),
    (re.compile(r"\"password\"\s*:\s*\"[^\"]+\""), "password"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "APIキー"),
)


def check_catalog(target: Path) -> list[str]:
    """catalog.jsonに行数等の統計が入っていないか見る。"""
    path = target / "catalog.json"
    if not path.is_file():
        return [f"{path} がありません。docs generateが失敗している可能性があります"]
    catalog = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for section in ("nodes", "sources"):
        for uid, node in catalog.get(section, {}).items():
            for key, stat in (node.get("stats") or {}).items():
                if key == "has_stats":
                    continue
                if not any(token in key.lower() for token in FORBIDDEN_STAT_KEYS):
                    continue
                if stat.get("value") not in (None, ""):
                    problems.append(f"{uid}: 統計 {key}={stat.get('value')} が含まれています")
    return problems


def _compiled_sources(target: Path) -> list[tuple[str, str]]:
    """compiled SQLを (識別子, 本文) で集める。manifestとファイルの両方を見る。"""
    found: list[tuple[str, str]] = []
    manifest = target / "manifest.json"
    if manifest.is_file():
        payload: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
        for section in ("nodes", "sources"):
            for uid, node in payload.get(section, {}).items():
                for field in ("compiled_code", "raw_code"):
                    code = node.get(field)
                    if code:
                        found.append((f"{uid}:{field}", code))
    for sql in (target / "compiled").rglob("*.sql"):
        found.append((str(sql.relative_to(target)), sql.read_text(encoding="utf-8")))
    return found


def check_literals(target: Path) -> list[str]:
    """SQLに銘柄コードのリテラルが埋まっていないか見る。"""
    problems: list[str] = []
    for name, code in _compiled_sources(target):
        tickers = sorted(set(TICKER_LITERAL.findall(code)))
        if tickers:
            problems.append(f"{name}: 銘柄コードらしいリテラル {', '.join(tickers)}")
    return problems


def check_secrets(target: Path) -> list[str]:
    """認証情報が混ざっていないか見る。"""
    problems: list[str] = []
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".html", ".sql", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{path.relative_to(target)}: {label} らしい文字列")
    return problems


def strip_stats(target: Path) -> list[str]:
    """catalog.jsonから行数等の統計だけを落とす。列情報は残す。

    **`--empty-catalog`を使わない。** あれはcatalog全体を空にするため、列名・型・説明まで
    消える。公開したいのはまさにその列情報なので、統計だけを外す。

    `table`でmaterializeしたモデルにだけ統計が付く（viewには無い）。実測では
    `fct_metric_comparison`が`num_rows=135708`、`num_bytes=59497162`を持っていた。
    値そのものではないが、取得データの規模を外部へ示すことになるため落とす。
    """
    path = target / "catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    removed: list[str] = []
    for section in ("nodes", "sources"):
        for uid, node in catalog.get(section, {}).items():
            stats = node.get("stats") or {}
            drop = [
                key
                for key in stats
                if key != "has_stats"
                and any(token in key.lower() for token in FORBIDDEN_STAT_KEYS)
            ]
            for key in drop:
                removed.append(f"{uid.split('.')[-1]}.{key}")
                del stats[key]
            if drop:
                stats["has_stats"] = {
                    "id": "has_stats",
                    "label": "Has Stats?",
                    "value": False,
                    "include": False,
                    "description": "Indicates whether there are statistics for this table",
                }
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return removed


def main() -> int:
    """成果物を検査し、実データが混ざっていれば非ゼロで終了する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="dbtのtargetディレクトリ")
    parser.add_argument(
        "--strip-stats",
        action="store_true",
        help="検査の前に、catalog.jsonの行数等の統計を落とす（列情報は残す）",
    )
    args = parser.parse_args()

    if not args.target.is_dir():
        print(f"{args.target} がありません", file=sys.stderr)
        return 1

    if args.strip_stats:
        removed = strip_stats(args.target)
        print(f"統計を除去: {len(removed)}件 {', '.join(removed) if removed else ''}")

    checks = (
        ("行数等の統計", check_catalog(args.target)),
        ("銘柄コードのリテラル", check_literals(args.target)),
        ("認証情報", check_secrets(args.target)),
    )

    failed = False
    for label, problems in checks:
        if problems:
            failed = True
            print(f"NG {label}: {len(problems)}件", file=sys.stderr)
            for problem in problems:
                print(f"   {problem}", file=sys.stderr)
        else:
            print(f"OK {label}")

    if failed:
        print(
            "\n実データが成果物へ混ざっています。公開するとJ-Quantsの再配布禁止に抵触します。",
            file=sys.stderr,
        )
        return 1
    print("\ndbt docsにはメタデータとSQLロジックだけが含まれます。公開できます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
