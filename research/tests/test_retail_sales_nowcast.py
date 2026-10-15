from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from research.retail_sales_nowcast import (
    build_panel,
    read_monthly_series,
    run_expanding_window,
    summarize,
)


def _write_series(path, rows, *, retrieved="2026-10-01"):
    lines = ["month,yoy_pct,retrieved_as_of"]
    lines += [f"{month},{value},{retrieved}" for month, value in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _months(count, start_year=2024):
    out = []
    year, month = start_year, 1
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def test_month_must_be_year_month(tmp_path):
    path = _write_series(tmp_path / "s.csv", [("2024-01-15", 1.0)])

    with pytest.raises(ValueError, match="YYYY-MM"):
        read_monthly_series(path, label="企業月次")


def test_duplicate_month_is_rejected(tmp_path):
    path = _write_series(tmp_path / "s.csv", [("2024-01", 1.0), ("2024-01", 2.0)])

    with pytest.raises(ValueError, match="同じ月が複数"):
        read_monthly_series(path, label="企業月次")


def test_missing_retrieved_as_of_is_rejected(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("month,yoy_pct\n2024-01,1.0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="retrieved_as_of"):
        read_monthly_series(path, label="企業月次")


def test_industry_is_lagged_so_the_month_it_was_not_published_is_not_used(tmp_path):
    """月tの行には月t-1の統計が入る。企業開示時点で未公表の数字を使わない。"""
    company = read_monthly_series(
        _write_series(tmp_path / "c.csv", [("2024-01", 1.0), ("2024-02", 2.0), ("2024-03", 3.0)]),
        label="企業月次",
    )
    industry = read_monthly_series(
        _write_series(tmp_path / "i.csv", [("2024-01", 10.0), ("2024-02", 20.0), ("2024-03", 30.0)]),
        label="業界統計",
    )

    panel = build_panel(company, industry, lag=1)

    by_month = panel.set_index("month")
    assert pd.isna(by_month.loc["2024-01", "industry_yoy_pct"])  # 2023-12の統計は入力に無い
    assert by_month.loc["2024-02", "industry_yoy_pct"] == pytest.approx(10.0)
    assert by_month.loc["2024-03", "industry_yoy_pct"] == pytest.approx(20.0)


def test_forecast_does_not_change_when_future_months_are_appended(tmp_path):
    """**先読みをしていないことの検証。**

    月tの予測は、月t以降のデータが手元にあるかどうかで変わってはならない。全期間で
    1回fitする実装だと、後から足した月が過去の予測を書き換えてしまう。
    """
    months = _months(20)
    rng = np.random.default_rng(0)
    industry_values = list(np.round(rng.normal(2.0, 3.0, len(months)), 3))
    company_values = [round(1.5 * v + 0.5, 3) for v in industry_values]

    def forecasts_for(upto: int):
        company = read_monthly_series(
            _write_series(
                tmp_path / f"c{upto}.csv", list(zip(months[:upto], company_values[:upto], strict=True))
            ),
            label="企業月次",
        )
        industry = read_monthly_series(
            _write_series(
                tmp_path / f"i{upto}.csv", list(zip(months[:upto], industry_values[:upto], strict=True))
            ),
            label="業界統計",
        )
        panel = build_panel(company, industry, lag=1)
        return run_expanding_window(panel, min_train=12).set_index("month")

    short = forecasts_for(16)
    long = forecasts_for(20)

    shared = short.index.intersection(long.index)
    assert len(shared) >= 2
    for month in shared:
        assert short.loc[month, "forecast_model_pct"] == pytest.approx(
            long.loc[month, "forecast_model_pct"]
        )


def test_a_perfectly_linear_relationship_beats_naive():
    """業界統計が企業売上を線形に決めている合成データでは、naiveに勝つ。"""
    months = _months(30)
    rng = np.random.default_rng(1)
    industry = np.round(rng.normal(0.0, 5.0, len(months)), 3)
    panel = pd.DataFrame(
        {
            "month": months,
            "month_start": pd.to_datetime(months, format="%Y-%m"),
            "company_yoy_pct": 2.0 * industry + 1.0,
            "industry_yoy_pct": industry,
            "company_retrieved_as_of": pd.Timestamp("2026-10-01"),
            "industry_retrieved_as_of": pd.Timestamp("2026-10-01"),
        }
    )

    summary = summarize(run_expanding_window(panel, min_train=12))

    assert summary["model_beats_mean"]
    assert summary["mae_ratio_vs_mean"] < 0.5


def test_unrelated_industry_series_does_not_beat_the_mean_baseline():
    """**無関係な説明変数で「改善した」と報告しないことの検証。**

    前月横置きをベースラインにすると、前年比系列では無関係な変数でもMAE比が0.71前後に
    なり勝ってしまう。切片だけのモデルと比べれば、余計なパラメータを推定するぶん
    わずかに悪くなるだけで、改善したとは報告されない。
    """
    months = _months(60)
    rng = np.random.default_rng(2)
    panel = pd.DataFrame(
        {
            "month": months,
            "month_start": pd.to_datetime(months, format="%Y-%m"),
            "company_yoy_pct": rng.normal(0.0, 1.0, len(months)),
            "industry_yoy_pct": rng.normal(0.0, 1.0, len(months)),
            "company_retrieved_as_of": pd.Timestamp("2026-10-01"),
            "industry_retrieved_as_of": pd.Timestamp("2026-10-01"),
        }
    )

    summary = summarize(run_expanding_window(panel, min_train=12))

    assert not summary["model_beats_mean"]
    # 前月横置きとの比較だと、無関係でも勝ってしまうことを明示的に残す
    assert summary["mae_ratio_vs_naive"] < 0.9


def test_too_few_overlapping_months_fails_loudly():
    """重なる月が足りないとき、少ないデータで黙って予測しない。"""
    months = _months(5)
    panel = pd.DataFrame(
        {
            "month": months,
            "month_start": pd.to_datetime(months, format="%Y-%m"),
            "company_yoy_pct": [1.0, 2.0, 3.0, 4.0, 5.0],
            "industry_yoy_pct": [1.0, 2.0, 3.0, 4.0, 5.0],
            "company_retrieved_as_of": pd.Timestamp("2026-10-01"),
            "industry_retrieved_as_of": pd.Timestamp("2026-10-01"),
        }
    )

    with pytest.raises(ValueError, match="予測できる月がありません"):
        run_expanding_window(panel, min_train=12)
