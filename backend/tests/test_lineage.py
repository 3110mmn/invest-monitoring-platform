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
