import json

import pytest

from app.etl.fetchers.jquants import JQuantsClient, JQuantsError
from app.etl.normalizers import normalize_jquants_price
from app.etl.pipeline import JQuantsMarketPipeline
from app.etl.validators import validate_price


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class RawResponse(FakeResponse):
    def read(self):
        return self.payload


def test_jquants_client_follows_pagination():
    payloads = iter([
        {"data": [{"Code": "86970"}], "pagination_key": "next"},
        {"data": [{"Code": "72030"}]},
    ])
    urls = []

    def opener(request, timeout):
        urls.append((request.full_url, timeout, request.headers["X-api-key"]))
        return FakeResponse(next(payloads))

    client = JQuantsClient("secret", opener=opener, sleep=lambda _seconds: None)
    records = client.equities_master(date="2026-08-27")

    assert [record["Code"] for record in records] == ["86970", "72030"]
    assert "pagination_key=next" in urls[1][0]
    assert urls[0][2] == "secret"


def test_jquants_client_accepts_empty_data_array():
    client = JQuantsClient(
        "secret", opener=lambda *_args, **_kwargs: FakeResponse({"data": []})
    )

    assert client.equities_master() == []


@pytest.mark.parametrize("body", [b"not-json", b"\xff"])
def test_jquants_client_wraps_invalid_response_body(body):
    client = JQuantsClient(
        "secret", opener=lambda *_args, **_kwargs: RawResponse(body)
    )

    with pytest.raises(JQuantsError, match="invalid JSON response"):
        client.equities_master()


def test_jquants_client_rejects_missing_data():
    client = JQuantsClient(
        "secret", opener=lambda *_args, **_kwargs: FakeResponse({"pagination_key": "next"})
    )

    with pytest.raises(JQuantsError, match="missing 'data'"):
        client.equities_master()


def test_jquants_client_rejects_non_array_data():
    client = JQuantsClient(
        "secret", opener=lambda *_args, **_kwargs: FakeResponse({"data": {}})
    )

    with pytest.raises(JQuantsError, match="must be an array"):
        client.equities_master()


def test_jquants_client_rejects_invalid_record_in_page():
    client = JQuantsClient(
        "secret",
        opener=lambda *_args, **_kwargs: FakeResponse(
            {"data": [{"Code": "86970"}, None, "invalid"]}
        ),
    )

    with pytest.raises(JQuantsError, match=r"indexes=\[1, 2\]"):
        client.equities_master()


def test_jquants_price_normalization_and_validation():
    record = normalize_jquants_price({
        "Code": "86970", "Date": "2026-08-26",
        "AdjO": 100, "AdjH": 110, "AdjL": 95, "AdjC": 105, "AdjVo": 1234,
    })
    assert record.obs_date == "2026-08-26"
    assert record.close_price == 105.0
    assert record.volume == 1234.0
    assert validate_price(record) == []


def test_invalid_ohlc_is_rejected():
    record = normalize_jquants_price({
        "Code": "86970", "Date": "2026-08-26",
        "AdjO": 100, "AdjH": 90, "AdjL": 95, "AdjC": 105, "AdjVo": -1,
    })
    assert {issue.error_type for issue in validate_price(record)} == {
        "invalid_high", "invalid_low", "negative_volume"
    }


def test_pipeline_records_raw_and_ingestion_metadata(db, tmp_path):
    class FakeClient:
        base_url = "https://example.invalid/v2"

        def equities_master(self, **_kwargs):
            return [{"Code": "86970", "CoName": "JPX", "ProdCat": "011", "Mkt": "0111"}]

    pipeline = JQuantsMarketPipeline(db, FakeClient(), tmp_path / "raw", tmp_path)
    result = pipeline.sync_master()

    run = db.execute("SELECT * FROM ingestion_run WHERE ingestion_run_id = ?", (result["run_id"],)).fetchone()
    assert run["status"] == "succeeded"
    assert run["job_type"] == "jquants_equities_master_v1"
    assert run["raw_path"].endswith(".json.gz")
    assert (tmp_path / run["raw_path"]).exists()


def _http_error(code, headers=None):
    """テスト用のHTTPErrorを組み立てる。"""
    import email.message
    from urllib.error import HTTPError

    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return HTTPError("https://example.test", code, "error", message, None)


class _FakeClock:
    """sleepした分だけ時刻が進む時計。待機の合計と経過を実際の挙動に合わせる。"""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _client(clock, opener, **kwargs):
    return JQuantsClient(
        "secret", opener=opener, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs
    )


def test_client_paces_requests_to_the_plan_rate_limit():
    """1分あたりの上限から送信間隔を決め、間隔が空くまで次の送信を待つ。"""
    clock = _FakeClock()
    payloads = iter([
        {"data": [{"Code": "1"}], "pagination_key": "next"},
        {"data": [{"Code": "2"}]},
    ])

    def opener(*_args, **_kwargs):
        clock.advance(1.0)  # 通信に1秒かかったとみなす
        return FakeResponse(next(payloads))

    client = _client(clock, opener, requests_per_minute=5)
    client.equities_master()

    # 5回/分 → 12秒間隔。前回送信から1秒経過しているため残り11秒待つ。
    assert clock.slept == [11.0]


def test_client_does_not_pace_when_rate_limit_is_disabled():
    """レート制限を0に設定した場合は待たない。"""
    clock = _FakeClock()
    payloads = iter([
        {"data": [{"Code": "1"}], "pagination_key": "next"},
        {"data": [{"Code": "2"}]},
    ])
    client = _client(clock, lambda *_a, **_k: FakeResponse(next(payloads)), requests_per_minute=0)

    client.equities_master()

    assert clock.slept == []


def test_client_waits_for_the_rate_limit_window_on_429():
    """429はRetry-Afterが無ければ制限窓が明けるまで待つ。秒単位の指数バックオフでは足りない。"""
    clock = _FakeClock()
    responses = [_http_error(429), FakeResponse({"data": []})]

    def opener(*_args, **_kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    client = _client(clock, opener, requests_per_minute=5)

    assert client.equities_master() == []
    assert clock.slept == [60.0]


def test_client_honors_retry_after_header():
    """Retry-Afterが返ればその秒数に従う。"""
    clock = _FakeClock()
    responses = [_http_error(429, {"Retry-After": "30"}), FakeResponse({"data": []})]

    def opener(*_args, **_kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    client = _client(clock, opener, requests_per_minute=5)
    client.equities_master()

    assert clock.slept == [30.0]
