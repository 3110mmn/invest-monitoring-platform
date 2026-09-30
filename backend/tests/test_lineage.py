"""rawのローカル保存が、取得元をまたいで同じ規則で行われることを確認する。"""

import gzip
import json

from app.etl.lineage import stage_raw


def test_raw_is_written_under_the_source_key(tmp_path):
    """取得元をパスへ含める。GCSへ上げた後に取得元をまたいで探せるようにするため。"""
    path = stage_raw(tmp_path, "yfinance", "yfinance_daily_prices_v1", 12, [{"a": 1}])

    assert "yfinance" in path.parts
    assert "yfinance_daily_prices_v1" in path.parts
    assert path.name == "run-12.json.gz"


def test_raw_content_round_trips(tmp_path):
    """保存したrawは読み戻せる。読めない保存は保存していないのと同じ。"""
    records = [{"target_key": "7203.T", "close": 3025.0}]

    path = stage_raw(tmp_path, "yfinance", "job", 1, records)

    with gzip.open(path, "rt", encoding="utf-8") as stream:
        assert json.load(stream) == records


def test_staging_raw_warns_when_there_is_nowhere_durable_to_publish(tmp_path, monkeypatch, capsys):
    """公開先が未設定なら警告する。rawは原本で、手元だけに残ると再構築の前提が崩れる。

    実際に2年分の財務バックフィルを手元で流したとき、`RAW_DATA_URI` が未設定だった
    ために487ファイルがこのマシンにしか無い状態が数日続いた。
    """
    from app.config import settings
    from app.etl import lineage

    monkeypatch.setattr(settings, "raw_data_uri", None)
    monkeypatch.setattr(lineage, "_warned_about_unpublished_raw", False)

    lineage.stage_raw(tmp_path, "yfinance", "job", 1, [{"a": 1}])

    assert "RAW_DATA_URI" in capsys.readouterr().out


def test_staging_raw_is_quiet_when_a_destination_is_configured(tmp_path, monkeypatch, capsys):
    """公開先があるときは黙る。毎回出ると本当に見たい行が埋もれる。"""
    from app.config import settings
    from app.etl import lineage

    monkeypatch.setattr(settings, "raw_data_uri", "gs://bucket/raw")
    monkeypatch.setattr(lineage, "_warned_about_unpublished_raw", False)

    lineage.stage_raw(tmp_path, "yfinance", "job", 1, [{"a": 1}])

    assert "RAW_DATA_URI" not in capsys.readouterr().out


def test_the_warning_is_printed_only_once(tmp_path, monkeypatch, capsys):
    """1回の実行で何度も出さない。銘柄ごとに出ると読めなくなる。"""
    from app.config import settings
    from app.etl import lineage

    monkeypatch.setattr(settings, "raw_data_uri", None)
    monkeypatch.setattr(lineage, "_warned_about_unpublished_raw", False)

    for run_id in (1, 2, 3):
        lineage.stage_raw(tmp_path, "yfinance", "job", run_id, [{"a": 1}])

    assert capsys.readouterr().out.count("RAW_DATA_URI") == 1
