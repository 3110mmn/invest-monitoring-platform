"""Japan-REIT.comの利回り一覧CSVとコード対応表から、用途分類の入力CSVを作る。

`reit_rate_sensitivity.py` が読むのは `reit_asset_types.csv` の1本だけである。このスクリプトは
その生成だけを担い、分析には関与しない。入力・出力ともGit管理外（`research/inputs/*`）。

**自動取得はしない。** 利回り一覧CSVは手元へダウンロードしたものを渡す。コード対応表も
サイト上のラベルを見て手で埋める。推測で埋めると、誤った分類のまま回帰が回って気づけない。

使い方:

    cp research/inputs/reit_asset_type_codes.example.csv \\
       research/inputs/reit_asset_type_codes.csv
    # サイトの表記を見てコードとラベルを記入する
    python research/build_reit_asset_types.py \\
        --source research/inputs/JREITcom_20261007.csv \\
        --codes research/inputs/reit_asset_type_codes.csv \\
        --out research/inputs/reit_asset_types.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "research"))

from reit_rate_sensitivity import ASSET_TYPES, normalize_jpx_code  # noqa: E402

# 利回り一覧CSVの列名。サイトの表記が変わったら落ちるようにする。
SOURCE_CODE_COLUMN = "証券コード"
SOURCE_NAME_COLUMN = "投資法人"
SOURCE_ASSET_COLUMN = "運用資産"

OUTPUT_COLUMNS = [
    "jpx_code",
    "asset_type",
    "source_label",
    "classification_as_of",
    "source_name",
    "source_url",
]


def read_code_map(path: Path) -> dict[str, dict[str, str]]:
    """コード対応表を読む。空欄のまま残した行は「未確認」として扱い、使わせない。"""
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        raise ValueError(f"{path} が空です。サイトの表記を見て記入してください")
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        code = (row.get("source_code") or "").strip()
        if not code:
            continue
        asset_type = (row.get("asset_type") or "").strip().lower()
        if not asset_type:
            # 未確認のコードは登録しない。該当銘柄が出たら下で止まる。
            continue
        if asset_type not in ASSET_TYPES:
            raise ValueError(
                f"未対応のasset_typeです: {asset_type}（コード {code}）。"
                f"使えるのは {', '.join(sorted(ASSET_TYPES))}"
            )
        mapping[code] = {
            "asset_type": asset_type,
            "source_label": (row.get("source_label") or "").strip(),
            "source_name": (row.get("source_name") or "").strip(),
            "source_url": (row.get("source_url") or "").strip(),
            "observed_as_of": (row.get("observed_as_of") or "").strip(),
        }
    if not mapping:
        raise ValueError(f"{path} に有効な行がありません")
    return mapping


def build_rows(source: Path, code_map: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    """利回り一覧の各行へ用途分類を当てる。未知のコードがあれば中断する。"""
    text = source.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    missing = {SOURCE_CODE_COLUMN, SOURCE_ASSET_COLUMN} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(
            f"{source} に必要な列がありません: {', '.join(sorted(missing))}。"
            "サイトの出力形式が変わった可能性があります"
        )

    rows: list[dict[str, str]] = []
    unknown: dict[str, list[str]] = {}
    for record in reader:
        code = (record.get(SOURCE_ASSET_COLUMN) or "").strip()
        jpx_code = normalize_jpx_code(record.get(SOURCE_CODE_COLUMN))
        if code not in code_map:
            unknown.setdefault(code, []).append(
                f"{jpx_code} {(record.get(SOURCE_NAME_COLUMN) or '').strip()}"
            )
            continue
        entry = code_map[code]
        rows.append({
            "jpx_code": jpx_code,
            "asset_type": entry["asset_type"],
            "source_label": entry["source_label"],
            "classification_as_of": entry["observed_as_of"],
            "source_name": entry["source_name"],
            "source_url": entry["source_url"],
        })

    if unknown:
        lines = [
            f"  コード {code!r}: {', '.join(names[:3])}"
            + (f" 他{len(names) - 3}件" if len(names) > 3 else "")
            for code, names in sorted(unknown.items())
        ]
        raise ValueError(
            "対応表に無いコードがあります。サイトの表記を確認して追記してください:\n"
            + "\n".join(lines)
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="利回り一覧CSV（手元へ保存したもの）")
    parser.add_argument("--codes", type=Path, required=True, help="コードとasset_typeの対応表")
    parser.add_argument("--out", type=Path, required=True, help="出力する用途分類CSV")
    args = parser.parse_args()

    rows = build_rows(args.source, read_code_map(args.codes))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["asset_type"]] = counts.get(row["asset_type"], 0) + 1
    print(f"{args.out} へ {len(rows)}件を書き出しました")
    for asset_type, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {asset_type:12} {count:>3}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
