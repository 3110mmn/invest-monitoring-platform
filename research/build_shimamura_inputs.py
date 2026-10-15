"""しまむらの月次売上速報と商業動態統計を、ナウキャストの入力CSVへ変換する。

**手で整形しない。** 元ファイルの構造を読み取るコードを残すことで、翌月のデータが
増えたときに同じ手順で再生成できる。整形を手作業にすると、どの行をどう読んだかが
失われ、`retrieved_as_of`の意味も曖昧になる。

元ファイルはいずれもGit管理外（`research/inputs/`）。利用条件が取得元ごとに異なるため。

## 入力

- しまむら: 決算期ごとのCSV（`しまむら売上/*.csv`）。IRが公表する月次売上速報
- 商業動態統計: 経済産業省のExcel。`前年比（change）(月次M)`シートの
  「織物・衣服・身の回り品小売業」列

## 注意している点

**しまむらの「3月度」は3月21日〜4月20日である**（決算日2月20日、月次期間21日〜20日）。
暦月とずれるが、統計側は暦月なので厳密には一致しない。ずらすと対応が複雑になるうえ、
20日締めなら当月の寄与が約2/3を占めるため、**同月として扱う**。この近似は分析の制約として
`research/README.md`へ記録する。

**四半期・上期計・通期の行は落とす。** 月次だけを残す。

**前年比は「101.1」の形式で来るので、`yoy_pct`へは-100して渡す**（+1.1%）。
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "research" / "inputs"
SOURCE_DIR = INPUT_DIR / "しまむら分析"

# 商業動態統計の「織物・衣服・身の回り品小売業」列。見出し行から探すので位置は固定しない。
INDUSTRY_SHEET = "前年比（change）(月次M)"
INDUSTRY_LABEL = "織物・衣服・身の回り品小売業"

MONTH_PATTERN = re.compile(r"^(\d{1,2})月度$")


def read_shimamura(source_dir: Path) -> pd.DataFrame:
    """決算期ごとのCSVから、月次の既存店売上高前年比を取り出す。

    **ヘッダーの行は年によって1列ずれるが、データ行の並びは揃っている。** 2025/2026年2月期は
    2行目が`既存店,全店,...`で始まり、2027年2月期は先頭に空列が入って`,既存店,全店,...`に
    なる。一方どのファイルもデータ行は`3月度,<既存店>,<全店>,...`であり、既存店は常に列1に
    来る。したがってヘッダーの文字位置ではなく、1行目の`売上高`の位置を基準にする。
    """
    frames: list[pd.DataFrame] = []
    for path in sorted(source_dir.glob("*.csv")):
        fiscal_year = int(re.search(r"(\d{4})年2月期", path.name).group(1))
        raw = pd.read_csv(path, header=None, dtype=str, encoding="utf-8-sig")

        # 1行目の`売上高`がデータ行の既存店列を指す。ヘッダー2行目はずれることがある。
        top_row = raw.iloc[0].fillna("")
        matches = [i for i, v in enumerate(top_row) if str(v).strip() == "売上高"]
        if not matches:
            raise ValueError(f"{path.name}に『売上高』の見出しがありません")
        column = matches[0]

        for _, row in raw.iterrows():
            label = str(row.iloc[0]).strip()
            matched = MONTH_PATTERN.match(label)
            if not matched:
                continue  # 四半期・上期計・通期を落とす
            value = str(row.iloc[column]).strip()
            if not value or value in {"nan", "-"}:
                continue  # 未到来の月
            month = int(matched.group(1))
            # 決算期は前年3月から当年2月まで。3〜12月は前年、1〜2月は決算年。
            year = fiscal_year - 1 if month >= 3 else fiscal_year
            frames.append(
                pd.DataFrame(
                    [{"month": f"{year:04d}-{month:02d}", "yoy_pct": float(value) - 100.0}]
                )
            )

    if not frames:
        raise ValueError("しまむらの月次データを1件も読めませんでした")
    out = pd.concat(frames, ignore_index=True).sort_values("month")
    if out["month"].duplicated().any():
        duplicates = sorted(out.loc[out["month"].duplicated(), "month"])
        raise ValueError(f"同じ月が複数あります: {', '.join(duplicates)}")
    return out.reset_index(drop=True)


def read_industry(path: Path) -> pd.DataFrame:
    """商業動態統計のExcelから、織物・衣服・身の回り品小売業の月次前年比を取り出す。

    列位置は固定しない。見出し行から`INDUSTRY_LABEL`を探して列番号を決める。
    """
    raw = pd.read_excel(path, sheet_name=INDUSTRY_SHEET, header=None)

    column = None
    for row_index in range(10):
        for col_index in range(raw.shape[1]):
            if str(raw.iat[row_index, col_index]).strip() == INDUSTRY_LABEL:
                column = col_index
                break
        if column is not None:
            break
    if column is None:
        raise ValueError(f"『{INDUSTRY_LABEL}』の列が見つかりません")

    rows: list[dict[str, object]] = []
    for _, row in raw.iterrows():
        label = str(row.iloc[1]).strip()  # 「1980年1月」の形式
        matched = re.match(r"^(\d{4})年(\d{1,2})月$", label)
        if not matched:
            continue
        value = row.iloc[column]
        if pd.isna(value):
            continue
        rows.append(
            {
                "month": f"{int(matched.group(1)):04d}-{int(matched.group(2)):02d}",
                "yoy_pct": float(value) - 100.0,
            }
        )

    if not rows:
        raise ValueError("商業動態統計から月次データを読めませんでした")
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DIR)
    parser.add_argument("--out", type=Path, default=INPUT_DIR)
    parser.add_argument(
        "--retrieved-as-of",
        default=date.today().isoformat(),
        help="元ファイルを取得した日。統計は速報から確報へ改訂されるため必須",
    )
    args = parser.parse_args()

    company = read_shimamura(args.source / "しまむら売上")
    industry_files = list((args.source / "商業統計調査").glob("*.xlsx"))
    if not industry_files:
        raise ValueError("商業統計調査のExcelが見つかりません")
    industry = read_industry(industry_files[0])

    for frame, name in ((company, "company_monthly_sales"), (industry, "industry_monthly_sales")):
        frame = frame.copy()
        # 元の前年比は小数第1位までなので、引き算で出た浮動小数の桁を落とす
        frame["yoy_pct"] = frame["yoy_pct"].round(1)
        frame["retrieved_as_of"] = args.retrieved_as_of
        destination = args.out / f"{name}.csv"
        frame.to_csv(destination, index=False)
        period = f"{frame['month'].iloc[0]} 〜 {frame['month'].iloc[-1]}"
        print(f"{destination.name}: {len(frame):,}行（{period}）")

    overlap = set(company["month"]) & set(industry["month"])
    print(f"重なる月: {len(overlap)}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
