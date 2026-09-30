"""日次の価格取得を検証する。

**PostgreSQLへは価格を入れない。** この経路の成果物はrawであり、そこからParquetを
組み立てる。したがってここで見るのは「どこから取り直すか」と「取得結果をrawの形へ
正しく写せるか」である。

再開位置は分析層の最終観測日から決める。以前は `market_price_observation` を見ていたが、
そのテーブルは廃止した。
"""

from datetime import date, timedelta

import pandas as pd

from scripts.daily_update import (
    DEFAULT_LOOKBACK_DAYS,
    REFETCH_OVERLAP_DAYS,
    frame_to_records,
    latest_observations,
    resolve_fetch_start,
)


def _price_frame(dates, volume=True):
    data = {
        "Open": [100.0 + i for i in range(len(dates))],
        "High": [110.0 + i for i in range(len(dates))],
        "Low": [90.0 + i for i in range(len(dates))],
        "Close": [105.0 + i for i in range(len(dates))],
    }
    if volume:
        data["Volume"] = [1000.0 * (i + 1) for i in range(len(dates))]
    return pd.DataFrame(data, index=pd.to_datetime(dates))


def test_fetch_start_uses_default_lookback_without_observations():
    """観測が無い銘柄は既定の遡及日数から取得する。

    初期投入は日次更新の役割ではない。ここで長期間を取りにいかない。
    """
    start = resolve_fetch_start({}, "7203.T", today=date(2026, 9, 12), default_lookback_days=7)

    assert start == date(2026, 9, 5)


def test_fetch_start_resumes_from_last_observation_with_overlap():
    """最終観測日から重複分だけ遡って再取得する。

    固定窓では実行が飛んだ期間の穴が埋まらない。直近数日は訂正や確定遅れがあるため
    重ねて取り直す。
    """
    latest = {"7203.T": date(2026, 8, 20)}

    start = resolve_fetch_start(latest, "7203.T", today=date(2026, 9, 12))

    assert start == date(2026, 8, 20) - timedelta(days=REFETCH_OVERLAP_DAYS)


def test_fetch_start_is_decided_per_symbol():
    """銘柄ごとに最終観測日を判定する。まとめて1つの起点にしない。"""
    latest = {"7203.T": date(2026, 9, 10)}

    toyota = resolve_fetch_start(latest, "7203.T", today=date(2026, 9, 12))
    other = resolve_fetch_start(
        latest, "6758.T", today=date(2026, 9, 12), default_lookback_days=7
    )

    assert toyota == date(2026, 9, 7)
    assert other == date(2026, 9, 5)


def test_missing_analytics_layer_falls_back_to_the_default_lookback():
    """分析層へ繋がらなくても取得を止めない。

    取得が止まるより取りすぎる方へ倒す。rawは冪等に組み直せるが、取り漏らした日は
    後から気づきにくい。
    """
    assert latest_observations(None, "yfinance") == {}
    assert latest_observations("/nonexistent-lake", "yfinance") == {}


def test_all_fetched_rows_become_records_with_volume():
    """取得した全営業日をrawの行へ写し、出来高も残す。"""
    frame = _price_frame(["2026-09-08", "2026-09-09", "2026-09-10"])

    records = frame_to_records("7203.T", frame)

    assert [r["obs_date"] for r in records] == ["2026-09-08", "2026-09-09", "2026-09-10"]
    assert [r["volume"] for r in records] == [1000.0, 2000.0, 3000.0]
    assert all(r["target_key"] == "7203.T" for r in records)


def test_volume_is_null_when_the_source_omits_it():
    """出来高が無い取得元でもゼロで埋めない。"""
    records = frame_to_records("7203.T", _price_frame(["2026-09-08"], volume=False))

    assert records[0]["volume"] is None


def test_records_keep_the_four_prices_unchanged():
    """四本値は加工せずそのまま写す。再現できる範囲を正直に保つため。"""
    records = frame_to_records("7203.T", _price_frame(["2026-09-08"]))

    assert (records[0]["open"], records[0]["high"], records[0]["low"], records[0]["close"]) == (
        100.0,
        110.0,
        90.0,
        105.0,
    )


def test_default_lookback_is_short_enough_to_stay_a_daily_job():
    """既定の遡及は日次の範囲に留める。初期投入はバックフィルの役割である。"""
    assert DEFAULT_LOOKBACK_DAYS <= 14
