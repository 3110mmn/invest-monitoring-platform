"""業界統計から企業の月次既存店売上を予測し、予測誤差を測る。

分析はResearch専用。入力CSV・分析結果をPostgreSQL、GCS、APIへ書き込まない。

**このスクリプトの主題は予測精度ではなく、予測の検証可能性である。** 株価リターンは
ほぼ全部がノイズで、仮説が当たったのか偶然だったのかを生涯に数回しか確かめられない。
一方で月次売上は30日で答えが出るため、Observation → Assessment → Outcome → Learn を
年12周回せる。回る分析系を作ることが目的で、当てることではない。

設計上の肝は3つある。

1. **先読みをしない。** 月tの予測は月t未満の情報だけで作る。学習窓を拡大しながら1か月先を
   予測し続ける（expanding window out-of-sample）。全期間で1回fitして当てはまりを見るのは
   当時の判断の再現にならない。
2. **入れ子のベースラインと比べる。** 「誤差2.3%」には意味がない。業界統計が情報を足して
   いるかを測るには、同じ窓・同じ手順で「切片だけのモデル（過去平均）」を走らせ、それに
   勝てたかを見る。
   **前月横置き（random walk）をベースラインに使ってはいけない。** 前年比系列は自己相関が
   弱く、前月横置きは過去平均より√2倍ほど誤差が大きい。そのため業界統計が完全に無関係でも
   MAE比が0.71前後になり、「改善した」と誤って報告される。実際に合成データで再現した。
3. **vintageを持つ。** 商業動態統計は速報から確報へ改訂される。改訂後の値で過去を再学習
   すると、当時は見えなかった数字で判断したことになる。各観測に`retrieved_as_of`を持たせ、
   どの時点の値かを失わない。

**ラグの既定は1か月である。** 企業の月次開示は月末から数日後、商業動態統計の速報は約4週後
なので、企業が月tを開示する時点で業界統計の月tは存在しない。したがって月t-1の統計で月tを
予測する。この非対称性は、企業の月次売上が業界統計の先行指標になり得ることも意味する。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "research" / "inputs"
OUTPUT_DIR = PROJECT_ROOT / "research" / "outputs" / "retail_sales_nowcast"

SERIES_COLUMNS = {"month", "yoy_pct", "retrieved_as_of"}


def read_monthly_series(path: Path, *, label: str) -> pd.DataFrame:
    """月次の前年同月比系列を検証して読む。

    `month`は`YYYY-MM`、`yoy_pct`は前年同月比を%で表した実数（+3.2なら+3.2%）、
    `retrieved_as_of`はその値を取得した日付とする。欠測月は行を置かない。ゼロで埋めない。
    """
    frame = pd.read_csv(path, dtype={"month": "string"})
    missing = SERIES_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"{label}のCSVに必要な列がありません: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError(f"{label}のCSVに1行以上入力してください")

    frame["month"] = frame["month"].str.strip()
    parsed = pd.to_datetime(frame["month"], format="%Y-%m", errors="coerce")
    if parsed.isna().any():
        bad = frame.loc[parsed.isna(), "month"].tolist()
        raise ValueError(f"{label}のmonthは YYYY-MM 形式で入力してください: {bad}")
    frame["month_start"] = parsed

    frame["yoy_pct"] = pd.to_numeric(frame["yoy_pct"], errors="coerce")
    if frame["yoy_pct"].isna().any():
        raise ValueError(f"{label}のyoy_pctに数値でない行があります")

    frame["retrieved_as_of"] = pd.to_datetime(frame["retrieved_as_of"], errors="coerce")
    if frame["retrieved_as_of"].isna().any():
        raise ValueError(f"{label}のretrieved_as_ofに日付でない行があります")

    if frame["month"].duplicated().any():
        duplicates = sorted(frame.loc[frame["month"].duplicated(), "month"].unique())
        raise ValueError(f"{label}のCSVに同じ月が複数あります: {', '.join(duplicates)}")

    return frame.sort_values("month_start").reset_index(drop=True)[
        ["month", "month_start", "yoy_pct", "retrieved_as_of"]
    ]


def build_panel(company: pd.DataFrame, industry: pd.DataFrame, *, lag: int) -> pd.DataFrame:
    """企業の月次系列へ、`lag`か月前の業界統計を結合する。

    **月tの行には月t-lagの統計を置く。** 同じ月の統計を置くと、企業が開示した時点では
    まだ公表されていない数字を使うことになる。
    """
    if lag < 0:
        raise ValueError("lagは0以上である必要があります")

    shifted = industry[["month_start", "yoy_pct", "retrieved_as_of"]].copy()
    shifted["month_start"] = shifted["month_start"] + pd.DateOffset(months=lag)
    shifted = shifted.rename(
        columns={"yoy_pct": "industry_yoy_pct", "retrieved_as_of": "industry_retrieved_as_of"}
    )

    panel = company.rename(
        columns={"yoy_pct": "company_yoy_pct", "retrieved_as_of": "company_retrieved_as_of"}
    ).merge(shifted, on="month_start", how="left", validate="one_to_one")
    panel["previous_company_yoy_pct"] = panel["company_yoy_pct"].shift()
    return panel


def _fit_and_predict(
    train_industry: np.ndarray, train_company: np.ndarray, target_industry: float
) -> float:
    """単回帰を最小二乗で解き、1点を予測する。

    説明変数に変動が無い窓では傾きが決まらないため、平均を返す。ゼロ割りを黙って通さない。
    """
    if train_industry.std() == 0:
        return float(train_company.mean())
    design = np.column_stack([np.ones_like(train_industry), train_industry])
    coefficients, *_ = np.linalg.lstsq(design, train_company, rcond=None)
    intercept, slope = float(coefficients[0]), float(coefficients[1])
    return intercept + slope * target_industry


def run_expanding_window(panel: pd.DataFrame, *, min_train: int = 12) -> pd.DataFrame:
    """月tを、月t未満の情報だけで予測し続ける。

    `min_train`か月ぶんの実績が揃った月から予測を始める。各行は、その月の時点で作れた
    予測と、後から判明した実績を持つ。
    """
    if min_train < 2:
        raise ValueError("min_trainは2以上である必要があります")

    usable = panel.dropna(subset=["company_yoy_pct", "industry_yoy_pct"]).reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for position in range(min_train, len(usable)):
        train = usable.iloc[:position]
        target = usable.iloc[position]
        actual = float(target["company_yoy_pct"])
        # 切片だけのモデル。業界統計が情報を足しているかは、これに勝てたかで判断する。
        baseline = float(train["company_yoy_pct"].mean())
        naive = float(train["company_yoy_pct"].iloc[-1])
        model = _fit_and_predict(
            train["industry_yoy_pct"].to_numpy(dtype=float),
            train["company_yoy_pct"].to_numpy(dtype=float),
            float(target["industry_yoy_pct"]),
        )
        rows.append(
            {
                "month": target["month"],
                "train_months": len(train),
                "industry_yoy_pct": float(target["industry_yoy_pct"]),
                "actual_company_yoy_pct": actual,
                "forecast_model_pct": model,
                "forecast_mean_pct": baseline,
                "forecast_naive_pct": naive,
                "error_model_pct": model - actual,
                "error_mean_pct": baseline - actual,
                "error_naive_pct": naive - actual,
                "company_retrieved_as_of": target["company_retrieved_as_of"],
                "industry_retrieved_as_of": target["industry_retrieved_as_of"],
            }
        )
    if not rows:
        raise ValueError(
            f"予測できる月がありません。業界統計と企業系列が重なる月が {min_train + 1} か月以上必要です"
        )
    return pd.DataFrame(rows)


def summarize(forecasts: pd.DataFrame) -> dict[str, Any]:
    """業界統計が情報を足しているかを要約する。

    **判定は切片だけのモデル（過去平均）との比で行う。** 絶対誤差の水準だけを報告すると
    当たっているように見え、前月横置きと比べると無関係な変数でも勝ってしまう。
    `naive`は参考値として残す。
    """

    def mae(column: str) -> float:
        return float(forecasts[column].abs().mean())

    def rmse(column: str) -> float:
        return float(np.sqrt((forecasts[column] ** 2).mean()))

    model_mae, mean_mae, naive_mae = mae("error_model_pct"), mae("error_mean_pct"), mae("error_naive_pct")
    return {
        "forecasts": len(forecasts),
        "period": f"{forecasts['month'].iloc[0]} 〜 {forecasts['month'].iloc[-1]}",
        "model_mae_pt": model_mae,
        "mean_mae_pt": mean_mae,
        "naive_mae_pt": naive_mae,
        # 1未満なら業界統計が過去平均より良い。これが本命の指標。
        "mae_ratio_vs_mean": model_mae / mean_mae if mean_mae else float("nan"),
        "mae_ratio_vs_naive": model_mae / naive_mae if naive_mae else float("nan"),
        "model_rmse_pt": rmse("error_model_pct"),
        "mean_rmse_pt": rmse("error_mean_pct"),
        "naive_rmse_pt": rmse("error_naive_pct"),
        "model_beats_mean": model_mae < mean_mae,
        "model_bias_pt": float(forecasts["error_model_pct"].mean()),
    }


def run_analysis(
    *,
    company_path: Path,
    industry_path: Path,
    output_dir: Path,
    lag: int = 1,
    min_train: int = 12,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    company = read_monthly_series(company_path, label="企業月次")
    industry = read_monthly_series(industry_path, label="業界統計")
    panel = build_panel(company, industry, lag=lag)
    unmatched = panel["industry_yoy_pct"].isna().sum()
    if unmatched:
        print(f"警告: 業界統計が対応しない月が{unmatched}件あります（予測から除外）", file=sys.stderr)

    forecasts = run_expanding_window(panel, min_train=min_train)
    summary = summarize(forecasts)

    output_dir.mkdir(parents=True, exist_ok=True)
    forecasts.to_csv(output_dir / "forecasts.csv", index=False)
    pd.DataFrame([summary]).to_csv(output_dir / "summary.csv", index=False)

    print(f"予測した月: {summary['forecasts']}件（{summary['period']}）  ラグ{lag}か月")
    print(f"  業界統計モデル    MAE {summary['model_mae_pt']:.2f}pt / RMSE {summary['model_rmse_pt']:.2f}pt")
    print(f"  過去平均(基準)    MAE {summary['mean_mae_pt']:.2f}pt / RMSE {summary['mean_rmse_pt']:.2f}pt")
    print(f"  前月横置(参考)    MAE {summary['naive_mae_pt']:.2f}pt / RMSE {summary['naive_rmse_pt']:.2f}pt")
    verdict = "業界統計が情報を足している" if summary["model_beats_mean"] else "**過去平均に勝てていない**"
    print(f"  MAE比 vs 過去平均: {summary['mae_ratio_vs_mean']:.3f}  → {verdict}")
    print(f"  （参考 vs 前月横置: {summary['mae_ratio_vs_naive']:.3f}。前年比系列ではこちらは常に有利に出る）")
    print(f"  モデルの偏り: {summary['model_bias_pt']:+.2f}pt")
    print(f"結果はローカル専用ディレクトリへ保存しました: {output_dir}")
    return forecasts, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", type=Path, default=INPUT_DIR / "company_monthly_sales.csv")
    parser.add_argument("--industry", type=Path, default=INPUT_DIR / "industry_monthly_sales.csv")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--lag",
        type=int,
        default=1,
        help="業界統計を何か月ずらして使うか。既定1（統計の公表が企業開示より遅いため）",
    )
    parser.add_argument("--min-train", type=int, default=12, help="予測を始めるのに必要な実績月数")
    args = parser.parse_args()
    run_analysis(
        company_path=args.company,
        industry_path=args.industry,
        output_dir=args.out,
        lag=args.lag,
        min_train=args.min_train,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
