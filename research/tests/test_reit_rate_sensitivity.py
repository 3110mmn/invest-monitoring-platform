from __future__ import annotations

import pandas as pd
import pytest

from research.reit_rate_sensitivity import (
    build_weekly_panel,
    drop_corrupt_prices,
    load_reit_prices,
    normalize_jpx_code,
    read_asset_types,
    read_mof_jgb_csv,
    summarize_asset_types,
    to_yahoo_symbol,
)


def test_normalize_jpx_code_accepts_four_or_five_digits() -> None:
    assert normalize_jpx_code("8951") == "89510"
    assert normalize_jpx_code("89510") == "89510"
    assert normalize_jpx_code("8951.0") == "89510"


@pytest.mark.parametrize("value", ["89511", "abc", "123"])
def test_normalize_jpx_code_rejects_unsupported_code(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_jpx_code(value)


def test_read_asset_types_validates_keys_and_source(tmp_path) -> None:
    path = tmp_path / "asset_types.csv"
    path.write_text(
        "jpx_code,asset_type,source_label,classification_as_of,source_name,source_url\n"
        "8951,office,Office,2025-08-31,JPX,https://example.test/source\n",
        encoding="utf-8",
    )

    result = read_asset_types(path)

    assert result.loc[0, "jpx_code"] == "89510"
    assert result.loc[0, "asset_type"] == "office"


def test_read_mof_jgb_csv_skips_preamble_and_missing_yields(tmp_path) -> None:
    path = tmp_path / "jgb.csv"
    path.write_text(
        "Interest Rate,,,,,(Unit : %)\n"
        "Date,1Y,2Y,10Y\n"
        "2024/1/5,0.1,0.2,0.5\n"
        "2024/1/6,0.1,0.2,-\n"
        "2024/1/8,0.1,0.2,0.6\n",
        encoding="utf-8",
    )

    result = read_mof_jgb_csv(path)

    assert result.to_dict("records") == [
        {"date": pd.Timestamp("2024-01-05").date(), "yield_pct": 0.5},
        {"date": pd.Timestamp("2024-01-08").date(), "yield_pct": 0.6},
    ]


def test_yahoo_symbol_drops_the_jquants_check_digit() -> None:
    assert to_yahoo_symbol("89510") == "8951.T"
    # JPXは2024年から英数字コードを発行している。霞ヶ関ホテルリートは401A0 -> 401A.T
    assert to_yahoo_symbol("401A0") == "401A.T"


def test_cached_prices_are_reused_without_fetching(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "prices.csv"
    cache.write_text(
        "jpx_code,obs_date,close_price,adjusted_close_price,fetched_at\n"
        "89510,2024-01-05,100.0,98.0,2026-10-07T00:00:00+00:00\n",
        encoding="utf-8",
    )

    def fail(*args, **kwargs):  # pragma: no cover - 呼ばれないことが期待値
        raise AssertionError("キャッシュがあるのにyfinanceへ取得しに行った")

    monkeypatch.setattr("research.reit_rate_sensitivity.fetch_reit_prices", fail)

    result = load_reit_prices(["89510"], cache_path=cache, refresh=False)

    assert result.loc[0, "close_price"] == 100.0
    assert result.loc[0, "adjusted_close_price"] == 98.0


def test_corrupt_price_is_dropped_but_a_real_crash_is_kept() -> None:
    # 8951.Tの2007-07-06は終値2,690円で、前後はいずれも175,000円前後だった。
    # 一方2008年や2020年の30〜45%下落は実際の市場の動きなので残す。
    daily = pd.DataFrame(
        {
            "jpx_code": ["89510"] * 7,
            "obs_date": pd.to_datetime(
                [
                    "2007-07-02",
                    "2007-07-03",
                    "2007-07-04",
                    "2007-07-05",
                    "2007-07-06",
                    "2007-07-09",
                    "2007-07-10",
                ]
            ),
            "close_price": [176000.0, 179000.0, 176000.0, 175000.0, 2690.0, 175000.0, 176000.0],
            "adjusted_close_price": [
                89256.0,
                90778.0,
                89256.0,
                88749.0,
                1364.0,
                88749.0,
                89256.0,
            ],
        }
    )

    result = drop_corrupt_prices(daily)

    assert len(result) == 6
    assert pd.Timestamp("2007-07-06") not in set(result["obs_date"])

    crash = daily.copy()
    crash["close_price"] = [176000.0, 179000.0, 176000.0, 175000.0, 96000.0, 95000.0, 97000.0]
    assert len(drop_corrupt_prices(crash)) == 7


def test_weekly_panel_separates_total_return_from_price_return() -> None:
    # 89510は3週目に分配金があり、調整済み終値のほうが下落が小さい。
    # 89520は分配金がないので両者が一致する。
    prices = pd.DataFrame(
        [
            ("89510", "2024-01-05", 100.0, 100.0),
            ("89510", "2024-01-12", 110.0, 110.0),
            ("89510", "2024-01-19", 99.0, 101.2),
            ("89520", "2024-01-05", 100.0, 100.0),
            ("89520", "2024-01-11", 105.0, 105.0),
            ("89520", "2024-01-19", 110.0, 110.0),
        ],
        columns=["jpx_code", "obs_date", "close_price", "adjusted_close_price"],
    )
    asset_types = pd.DataFrame(
        [
            ("89510", "office", "Office", pd.Timestamp("2025-08-31").date(), "test", "https://example.test/a"),
            (
                "89520",
                "residential",
                "Residential",
                pd.Timestamp("2025-08-31").date(),
                "test",
                "https://example.test/b",
            ),
        ],
        columns=[
            "jpx_code",
            "asset_type",
            "source_label",
            "classification_as_of",
            "source_name",
            "source_url",
        ],
    )
    yields = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-05", "2024-01-10", "2024-01-12", "2024-01-19"]),
            "yield_pct": [0.5, 0.58, 0.6, 0.55],
        }
    )

    panel = build_weekly_panel(prices, asset_types, yields)

    office = panel[panel["asset_type"] == "office"].sort_values("week")
    assert office["price_return"].iloc[0] != office["price_return"].iloc[0]  # first week has no prior week
    assert office["total_return"].iloc[0] != office["total_return"].iloc[0]
    assert office["price_return"].iloc[1] == pytest.approx(0.10)
    assert office["total_return"].iloc[1] == pytest.approx(0.10)
    # 分配金がインカムぶんだけ下落を緩衝する。これが価格リターンでは見えない差である。
    assert office["price_return"].iloc[2] == pytest.approx(-0.10)
    assert office["total_return"].iloc[2] == pytest.approx(-0.08)
    assert office["yield_change_bp"].iloc[1] == pytest.approx(10.0)
    assert office["yield_change_bp"].iloc[2] == pytest.approx(-5.0)
    residential = panel[panel["asset_type"] == "residential"].sort_values("week")
    assert residential["total_return"].iloc[2] == pytest.approx(
        residential["price_return"].iloc[2]
    )
    assert residential["yield_change_bp"].iloc[1] == pytest.approx(8.0)
    assert residential["yield_age_days"].iloc[1] == 1


def test_summary_reports_group_statistics_without_overclaiming_beta() -> None:
    panel = pd.DataFrame(
        [
            ("office", pd.Period("2024-01-12", freq="W-FRI"), "89510", 0.012, 0.01, 10.0),
            ("office", pd.Period("2024-01-19", freq="W-FRI"), "89510", -0.018, -0.02, -5.0),
        ],
        columns=[
            "asset_type",
            "week",
            "jpx_code",
            "total_return",
            "price_return",
            "yield_change_bp",
        ],
    )

    summary = summarize_asset_types(panel)

    assert summary.loc[0, "observations"] == 2
    assert summary.loc[0, "beta_return_pct_per_100bp"] != summary.loc[0, "beta_return_pct_per_100bp"]
