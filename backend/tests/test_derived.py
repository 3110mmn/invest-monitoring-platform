"""Derivedの計算定義を検証する。

計算定義が正本なので、定義そのものが壊れていないかをここで固定する。
価格データは固定値で与え、Parquetの中身には依存させない。
"""

import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analytics import runner
from analytics.runner import (
    _ensure_ca_bundle,
    connect,
    connect_lake,
    load_definition,
    run,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_financial_parquet import build_schema as build_financial_schema
from build_parquet import PRICE_SCHEMA
from build_security_master import SECURITY_MASTER_SCHEMA


def _write_prices(tmp_path, rows: list[dict]) -> str:
    """テスト用のParquetを書き、globを返す。

    スキーマは実際の書き出しと同じものを使う。ここで縮めた定義を持つと、列を増やした
    ときにテストだけ通って本番で落ちる。
    """
    target = tmp_path / "observed" / "market_price" / "year=2026"
    target.mkdir(parents=True)
    table = pa.Table.from_pylist(rows, schema=PRICE_SCHEMA)
    pq.write_table(table, target / "part-0.parquet")
    return str(tmp_path / "observed" / "market_price" / "**" / "*.parquet")


def _price(
    day: int,
    close: float | None,
    *,
    code="72030",
    basis="adjusted",
    source="jquants",
    raw_close: float | None = None,
    adjustment_factor: float | None = 1.0,
) -> dict:
    """1行ぶんの観測。検証に関係しない列は既定値で埋める。"""
    return {
        "target_key": "7203.T",
        "jpx_code": code,
        "source_key": source,
        "obs_date": date(2026, 7, day),
        "raw_close_price": close if raw_close is None else raw_close,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 1000.0,
        "price_basis": basis,
        "adjustment_factor": adjustment_factor,
        "ingestion_run_id": 1,
        "built_at": datetime(2026, 7, 1, tzinfo=UTC),
    }


def _returns(tmp_path, rows) -> list[dict]:
    """daily_returnの結果を列名つきで返す。

    位置で参照すると、計算定義に列を足したときにテストが静かに別の列を見る。
    実際に `security_key` を追加したときそれが起きた。
    """
    glob = _write_prices(tmp_path, rows)
    relation = run("daily_return", parquet_glob=glob).order("obs_date")
    names = relation.columns
    return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]


def _write_financials(tmp_path, rows: list[dict]) -> None:
    schema = build_financial_schema()
    target = tmp_path / "observed" / "financial_summary" / "year=2026"
    target.mkdir(parents=True)
    complete = [{name: row.get(name) for name in schema.names} for row in rows]
    pq.write_table(pa.Table.from_pylist(complete, schema=schema), target / "part-0.parquet")


def _financial(
    disclosed_day: int,
    bps: float | None,
    *,
    period_end_day: int = 1,
    number: str = "1",
    scope: str = "consolidated",
) -> dict:
    return {
        "jpx_code": "72030",
        "target_key": "7203.T",
        "source_key": "jquants",
        "disclosure_number": number,
        "disclosed_date": date(2026, 7, disclosed_day),
        "disclosed_time": "15:00:00",
        "document_type": "FYFinancialStatements_Consolidated_JP",
        "fiscal_period_type": "FY",
        "period_start": date(2025, 7, 1),
        "period_end": date(2026, 7, period_end_day),
        "fiscal_year_start": date(2025, 7, 1),
        "fiscal_year_end": date(2026, 7, period_end_day),
        "accounting_standard": "JP",
        "reporting_scope": scope,
        "bps": bps,
        "source_record_hash": number,
        "ingestion_run_id": int(number),
        "built_at": datetime(2026, 7, disclosed_day, tzinfo=UTC),
    }


def _pbr_rows(tmp_path, prices: list[dict], financials: list[dict]) -> list[dict]:
    _write_prices(tmp_path, prices)
    _write_financials(tmp_path, financials)
    master = tmp_path / "reference" / "security_master"
    master.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([], schema=SECURITY_MASTER_SCHEMA),
        master / "part-0.parquet",
    )
    connection = connect_lake(str(tmp_path))
    relation = run("point_in_time_pbr", connection=connection).order("as_of_date")
    names = relation.columns
    return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]


def test_definition_file_exists():
    assert "daily_return" in load_definition("daily_return")


def test_unknown_definition_reports_available_names():
    with pytest.raises(FileNotFoundError, match="daily_return"):
        load_definition("no_such_definition")


def test_first_observation_has_no_return(tmp_path):
    """前日が無ければリターンは定義できない。0にしない。"""
    rows = _returns(tmp_path, [_price(1, 100.0)])

    assert rows[0]["daily_return"] is None


def test_return_is_computed_against_the_previous_observation(tmp_path):
    rows = _returns(tmp_path, [_price(1, 100.0), _price(2, 110.0)])

    assert rows[1]["daily_return"] == pytest.approx(0.10)


def test_missing_close_does_not_become_zero(tmp_path):
    """欠損をゼロで埋めない。終値が無い日はリターンも無い。"""
    rows = _returns(tmp_path, [_price(1, 100.0), _price(2, None), _price(3, 121.0)])

    assert rows[1]["daily_return"] is None


def test_gap_in_dates_uses_the_previous_observation_not_the_previous_day(tmp_path):
    """休場で日付が飛んでも、暦日ではなく観測の並びで前日を決める。"""
    rows = _returns(tmp_path, [_price(1, 100.0), _price(6, 110.0)])

    assert rows[1]["daily_return"] == pytest.approx(0.10)
    assert rows[1]["prev_obs_date"] == date(2026, 7, 1), "前回観測日を持ち回る"


def test_zero_previous_close_does_not_raise(tmp_path):
    """0除算でクエリ全体を落とさない。1銘柄の異常値で分析が止まると困る。"""
    rows = _returns(tmp_path, [_price(1, 0.0), _price(2, 110.0)])

    assert rows[1]["daily_return"] is None


def test_unadjusted_prices_are_excluded(tmp_path):
    """調整済みと未調整を混ぜない。分割の前後でリターンが跳ねる。"""
    rows = _returns(
        tmp_path,
        [_price(1, 100.0), _price(2, 110.0, basis="unadjusted")],
    )

    assert len(rows) == 1


def test_symbols_do_not_leak_into_each_other(tmp_path):
    """銘柄をまたいで前日を取らない。"""
    rows = _returns(
        tmp_path,
        [_price(1, 100.0), _price(1, 500.0, code="67580"), _price(2, 110.0)],
    )

    toyota = [r for r in rows if r["jpx_code"] == "72030"]
    assert toyota[-1]["daily_return"] == pytest.approx(0.10)


def test_jquants_is_preferred_when_both_sources_have_the_day(tmp_path):
    """同じ日に両方あればJ-Quantsを採る。公式で調整済み、訂正情報も持つため。"""
    rows = _returns(
        tmp_path,
        [_price(1, 100.0, source="jquants"), _price(1, 999.0, source="yfinance")],
    )

    assert len(rows) == 1
    assert rows[0]["source_key"] == "jquants"
    assert rows[0]["close_price"] == 100.0


def test_yfinance_fills_days_jquants_does_not_provide(tmp_path):
    """J-Quantsが無い日はyfinanceで埋める。契約プランの遅延分がここに当たる。"""
    rows = _returns(
        tmp_path,
        [_price(1, 100.0, source="jquants"), _price(2, 110.0, source="yfinance")],
    )

    assert [r["source_key"] for r in rows] == ["jquants", "yfinance"]
    assert rows[1]["daily_return"] == pytest.approx(0.10), "取得元をまたいでもリターンは連続する"


def test_duplicate_sources_do_not_double_count_in_the_window(tmp_path):
    """同じ日が2行あると窓関数が狂う。採用は1行に絞る。"""
    rows = _returns(
        tmp_path,
        [
            _price(1, 100.0, source="jquants"),
            _price(2, 110.0, source="jquants"),
            _price(2, 110.0, source="yfinance"),
        ],
    )

    assert len(rows) == 2
    assert rows[1]["daily_return"] == pytest.approx(0.10)


def test_point_in_time_pbr_uses_only_previously_disclosed_financials(tmp_path):
    """同日開示は混ぜず、翌取引日から使う。未来情報による先読みを防ぐ。"""
    rows = _pbr_rows(
        tmp_path,
        [_price(1, 100.0), _price(2, 90.0), _price(3, 80.0)],
        [_financial(2, 100.0)],
    )

    assert rows[1]["calculation_status"] == "financial_not_available"
    assert rows[1]["pbr"] is None
    assert rows[2]["pbr"] == pytest.approx(0.8)
    assert rows[2]["is_below_book_value"] is True


def test_point_in_time_pbr_aligns_bps_after_a_stock_split(tmp_path):
    """1:2分割後はBPSを半分にし、未調整終値と同じ株数基準でPBRを出す。"""
    rows = _pbr_rows(
        tmp_path,
        [
            _price(1, 100.0, raw_close=100.0),
            _price(2, 100.0, raw_close=100.0),
            _price(3, 60.0, raw_close=60.0, adjustment_factor=0.5),
        ],
        [_financial(2, 100.0)],
    )

    latest = rows[-1]
    assert latest["bps_adjustment_factor"] == pytest.approx(0.5)
    assert latest["adjusted_bps"] == pytest.approx(50.0)
    assert latest["pbr"] == pytest.approx(1.2)
    assert latest["is_below_book_value"] is False


def test_point_in_time_pbr_does_not_treat_non_positive_bps_as_value(tmp_path):
    rows = _pbr_rows(
        tmp_path,
        [_price(1, 100.0), _price(2, 90.0), _price(3, 80.0)],
        [_financial(2, -10.0)],
    )

    assert rows[-1]["calculation_status"] == "non_positive_bps"
    assert rows[-1]["pbr"] is None
    assert rows[-1]["is_below_book_value"] is None


def test_point_in_time_pbr_prefers_consolidated_on_the_same_day(tmp_path):
    rows = _pbr_rows(
        tmp_path,
        [_price(1, 100.0), _price(2, 90.0), _price(3, 80.0)],
        [
            _financial(2, 50.0, number="1", scope="non_consolidated"),
            _financial(2, 100.0, number="2", scope="consolidated"),
        ],
    )

    assert rows[-1]["reporting_scope"] == "consolidated"
    assert rows[-1]["pbr"] == pytest.approx(0.8)


def test_point_in_time_pbr_reports_missing_adjustment_data(tmp_path):
    rows = _pbr_rows(
        tmp_path,
        [
            _price(1, 100.0),
            _price(2, 90.0),
            _price(3, 80.0, adjustment_factor=None),
        ],
        [_financial(2, 100.0)],
    )

    assert rows[-1]["calculation_status"] == "invalid_adjustment_factor"
    assert rows[-1]["pbr"] is None


def test_local_glob_does_not_require_gcs_dependencies(tmp_path, monkeypatch):
    """ローカルパスではgcsfsを触らない。手元とCIを外部依存なしで動かすため。"""
    called = False

    def _fail(connection):
        nonlocal called
        called = True

    monkeypatch.setattr("analytics.runner._register_gcs", _fail)
    connect(_write_prices(tmp_path, [_price(1, 100.0)]))

    assert called is False


class _RecordingConnection:
    """DuckDB接続の代役。実際のI/Oを起こさずに手順だけを記録する。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, parameters=None):
        self.statements.append(statement)
        return self


def test_gcs_glob_registers_the_fsspec_filesystem(monkeypatch):
    """gs:// を渡したらgcsfsを差し込む。DuckDBのhttpfsへは落とさない。

    httpfsはGCSをS3互換として扱うためHMACキーを要求する。ADCで済ませる経路を保つ。
    登録はviewを張る前でなければならない。順序が逆だと最初の読みがhttpfsへ落ちる。
    """
    order: list[str] = []
    fake = _RecordingConnection()
    monkeypatch.setattr(runner.duckdb, "connect", lambda: fake)
    monkeypatch.setattr(
        runner, "_register_gcs", lambda connection: order.append("register")
    )

    connect("gs://bucket/lake/observed/market_price/**/*.parquet")
    order += ["view" for s in fake.statements if "CREATE OR REPLACE VIEW" in s]

    assert order == ["register", "view"]


def test_ca_bundle_is_filled_in_when_unset(monkeypatch):
    """SSL_CERT_FILE未設定なら補う。aiohttpはOpenSSLの既定パスしか見ないため。"""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    _ensure_ca_bundle()

    assert Path(os.environ["SSL_CERT_FILE"]).is_file()


def test_existing_ca_bundle_is_not_overwritten(monkeypatch):
    """利用者が指定したCA束を勝手に置き換えない。"""
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/custom.pem")
    _ensure_ca_bundle()

    assert os.environ["SSL_CERT_FILE"] == "/etc/ssl/custom.pem"


def test_shutdown_hook_is_registered_once(monkeypatch):
    """終了時GCのフックは1回だけ登録する。connectのたびに積み上げない。"""
    registered: list[object] = []
    monkeypatch.setattr("analytics.runner.atexit.register", registered.append)
    monkeypatch.setattr("analytics.runner._shutdown_hook_registered", False)

    runner._release_handles_before_shutdown()
    runner._release_handles_before_shutdown()

    assert len(registered) == 1, (
        "未回収のfsspecハンドルがあるとプロセスが終了しなくなるため必須だが、"
        "接続のたびに登録すると終了処理が積み上がる"
    )


def test_views_are_readable_from_another_session(tmp_path):
    """viewを別sessionから開いても実体が消えないこと。

    所在を `SET VARIABLE` で束縛したまま view を定義すると、catalogは共有される一方
    session変数は引き継がれず、実体が `read_parquet(NULL)` になる。DuckDBのUIも
    `cursor()` も別sessionなので、画面に「Summary unavailable」とだけ出て理由が
    分からない状態になる。実際にUIで踏んだ。
    """
    glob = _write_prices(tmp_path, [_price(1, 100.0)])
    connection = connect(glob)

    other = connection.cursor()

    assert other.execute("SELECT COUNT(*) FROM preferred_price").fetchone()[0] == 1


def _forecast_disclosure(
    disclosed_day: int,
    *,
    forecast_op: int | None = None,
    next_forecast_op: int | None = None,
    actual_op: int | None = None,
    revenue: int | None = None,
    period_type: str = "2Q",
    fiscal_year_end_year: int = 2026,
    number: str = "1",
    scope: str = "consolidated",
    standard: str = "JP",
    disclosed_time: str = "15:00:00",
) -> dict:
    """会社予想を載せた開示を1件作る。検証に関係しない列は既定値で埋める。"""
    return {
        "jpx_code": "72030",
        "target_key": "7203.T",
        "source_key": "jquants",
        "disclosure_number": number,
        "disclosed_date": date(2026, 7, disclosed_day),
        "disclosed_time": disclosed_time,
        "document_type": f"{period_type}FinancialStatements_Consolidated_JP",
        "fiscal_period_type": period_type,
        "period_start": date(fiscal_year_end_year - 1, 4, 1),
        "period_end": date(fiscal_year_end_year, 3, 31),
        "fiscal_year_start": date(fiscal_year_end_year - 1, 4, 1),
        "fiscal_year_end": date(fiscal_year_end_year, 3, 31),
        "accounting_standard": standard,
        "reporting_scope": scope,
        "forecast_operating_income": forecast_op,
        "next_forecast_operating_income": next_forecast_op,
        "operating_income": actual_op,
        "revenue": revenue,
        "source_record_hash": number,
        "ingestion_run_id": int(number),
        "built_at": datetime(2026, 7, disclosed_day, tzinfo=UTC),
    }


def _forecast_rows(tmp_path, disclosures: list[dict], name: str) -> list[dict]:
    """会社予想のDerivedを計算して行を返す。"""
    _write_financials(tmp_path, disclosures)
    prices = tmp_path / "observed" / "market_price" / "year=2026"
    prices.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([], schema=PRICE_SCHEMA), prices / "part-0.parquet")
    master = tmp_path / "reference" / "security_master"
    master.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([], schema=SECURITY_MASTER_SCHEMA), master / "part-0.parquet"
    )
    connection = connect_lake(str(tmp_path))
    connection.execute(
        "CREATE OR REPLACE VIEW point_in_time_forecast AS "
        + load_definition("point_in_time_forecast")
    )
    relation = connection.sql(load_definition(name))
    names = relation.columns
    return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]


def test_forecast_is_not_visible_on_its_disclosure_day(tmp_path):
    """予想が使えるのは開示の翌日から。

    開示時刻と市場の前後を日付だけでは判定できないため、保守側へ倒す。
    `point_in_time_pbr` と同じ規則。
    """
    rows = _forecast_rows(
        tmp_path, [_forecast_disclosure(10, forecast_op=500)], "point_in_time_forecast"
    )

    assert [row["as_of_date"] for row in rows] == [date(2026, 7, 11)]


def test_each_date_resolves_to_exactly_one_forecast(tmp_path):
    """有効期間が重ならず、任意の基準日で引ける予想が1件に決まること。

    ROW_NUMBERで「最新」を取る実装だと基準日を変えたときに壊れるため、
    `superseded_at` で区間を持たせている。その区間が連続していることを確かめる。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(1, forecast_op=100, number="1"),
            _forecast_disclosure(10, forecast_op=250, number="2"),
            _forecast_disclosure(20, forecast_op=750, number="3"),
        ],
        "point_in_time_forecast",
    )

    by_seq = {row["forecast_seq"]: row for row in rows}
    assert by_seq[1]["superseded_at"] == by_seq[2]["as_of_date"]
    assert by_seq[2]["superseded_at"] == by_seq[3]["as_of_date"]
    assert by_seq[3]["superseded_at"] is None

    on_date = date(2026, 7, 15)
    visible = [
        row
        for row in rows
        if row["as_of_date"] <= on_date
        and (row["superseded_at"] is None or on_date < row["superseded_at"])
    ]
    assert [row["forecast_operating_income"] for row in visible] == [250]


def test_later_forecast_does_not_change_earlier_point_in_time(tmp_path, tmp_path_factory):
    """予想を後から追加しても、過去時点で見えていた予想が変わらないこと。

    先読み防止の本体。将来の開示が過去の評価を書き換えるなら、この分析は成立しない。
    """
    before = _forecast_rows(
        tmp_path,
        [_forecast_disclosure(1, forecast_op=100, number="1")],
        "point_in_time_forecast",
    )
    after = _forecast_rows(
        tmp_path_factory.mktemp("after"),
        [
            _forecast_disclosure(1, forecast_op=100, number="1"),
            _forecast_disclosure(20, forecast_op=750, number="2"),
        ],
        "point_in_time_forecast",
    )

    on_date = date(2026, 7, 10)

    def visible(rows: list[dict]) -> list[int]:
        return [
            row["forecast_operating_income"]
            for row in rows
            if row["as_of_date"] <= on_date
            and (row["superseded_at"] is None or on_date < row["superseded_at"])
        ]

    assert visible(before) == visible(after) == [100]


def test_same_day_disclosures_are_ordered_deterministically(tmp_path):
    """同日に複数開示があっても1件に畳まれること。

    実データで102行が同日2件だった。日付だけでは順序が決まらないので、
    開示時刻と開示番号まで見る。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(
                10, forecast_op=100, number="1", disclosed_time="09:00:00"
            ),
            _forecast_disclosure(
                10, forecast_op=300, number="2", disclosed_time="15:00:00"
            ),
        ],
        "point_in_time_forecast",
    )

    assert [row["forecast_operating_income"] for row in rows] == [300]


def test_forecast_disclosed_after_actual_is_not_evaluated(tmp_path):
    """実績が出た後に出た予想は、採点に使われないこと。

    最後の予想を会社予想として評価すると、実測でMAPEが28.7%から17.0%へ下がる。
    4割の精度向上に見えるが全て後知恵である。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(1, forecast_op=100, number="1"),
            _forecast_disclosure(
                20,
                actual_op=900,
                revenue=10000,
                period_type="FY",
                number="2",
            ),
            # 実績開示より後に出た予想。採点に使ってはいけない。
            _forecast_disclosure(25, forecast_op=890, number="3"),
        ],
        "forecast_accuracy",
    )

    assert [row["forecast_operating_income"] for row in rows] == [100]


def test_error_is_scaled_by_revenue_not_operating_income(tmp_path):
    """誤差を売上でスケールすること。

    実績営業利益を分母にすると、着地がゼロ近傍の企業で誤差が発散する。実測では
    中央値16.8%に対して最大70,800%だった。売上ならゼロ近傍になりにくい。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(1, forecast_op=500, number="1"),
            # 営業利益がほぼゼロへ着地。MAPEなら4,900%になる。
            _forecast_disclosure(
                20, actual_op=10, revenue=10000, period_type="FY", number="2"
            ),
        ],
        "forecast_accuracy",
    )

    (row,) = rows
    assert row["error_pct_of_revenue"] == pytest.approx(-4.9)
    assert row["outcome"] == "miss"


def test_mismatched_accounting_standard_is_not_compared(tmp_path):
    """会計基準が違う予想と実績を比べないこと。

    基準が違うと営業利益の定義が変わる。0で埋めず理由を返す。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(1, forecast_op=500, number="1", standard="JP"),
            _forecast_disclosure(
                20,
                actual_op=600,
                revenue=10000,
                period_type="FY",
                number="2",
                standard="IFRS",
            ),
        ],
        "forecast_accuracy",
    )

    (row,) = rows
    assert row["comparison_status"] == "accounting_standard_mismatch"
    assert row["error_pct_of_revenue"] is None


def test_corrected_actual_does_not_replace_the_first_one(tmp_path):
    """実績の訂正開示が出ても、最初に出た実績で採点すること。

    訂正後の値を使うと、当時見えていなかった数字で予想を採点することになる。
    """
    rows = _forecast_rows(
        tmp_path,
        [
            _forecast_disclosure(1, forecast_op=500, number="1"),
            _forecast_disclosure(
                20, actual_op=600, revenue=10000, period_type="FY", number="2"
            ),
            _forecast_disclosure(
                25, actual_op=400, revenue=10000, period_type="FY", number="3"
            ),
        ],
        "forecast_accuracy",
    )

    (row,) = rows
    assert row["actual_operating_income"] == 600
