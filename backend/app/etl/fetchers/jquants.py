"""Minimal J-Quants V2 client with pagination and bounded retries."""

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class JQuantsError(RuntimeError):
    pass


class JQuantsClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.jquants.com/v2",
        timeout: float = 30.0,
        max_attempts: int = 3,
        requests_per_minute: int = 5,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("JQUANTS_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        # プランのレート制限は「1分あたりの回数」で決まるため、送信間隔をそこから導く。
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._opener = opener
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        """直前の送信から最小間隔が空くまで待つ。"""
        if self.min_interval <= 0 or self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            self._sleep(self.min_interval - elapsed)

    def _retry_delay(self, exc: HTTPError, attempt: int) -> float:
        """再試行までの待機秒数を決める。

        429 は1分あたりの上限に達した状態なので、秒単位のバックオフでは窓が空かない。
        `Retry-After` があればそれに従い、無ければ制限窓が明けるまで待つ。
        """
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
            return max(self.min_interval, 60.0)
        return 2 ** (attempt - 1)

    def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"x-api-key": self.api_key, "Accept": "application/json"})

        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            self._last_request_at = self._monotonic()
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    try:
                        body = response.read().decode("utf-8")
                        payload = json.loads(body)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise JQuantsError(
                            "J-Quants returned an invalid JSON response"
                        ) from exc
                if not isinstance(payload, dict):
                    raise JQuantsError("J-Quants response must be a JSON object")
                return payload
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.max_attempts:
                    raise JQuantsError(f"J-Quants HTTP error: {exc.code}") from exc
                delay = self._retry_delay(exc, attempt)
            except (URLError, TimeoutError) as exc:
                if attempt == self.max_attempts:
                    raise JQuantsError(f"J-Quants network error: {exc}") from exc
                delay = 2 ** (attempt - 1)
            self._sleep(delay)
        raise JQuantsError("J-Quants request failed")

    def fetch_all(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        records: list[dict[str, Any]] = []
        while True:
            payload = self._request(path, query)
            if "data" not in payload:
                raise JQuantsError("J-Quants response is missing 'data'")
            page = payload["data"]
            if not isinstance(page, list):
                raise JQuantsError("J-Quants response 'data' must be an array")
            invalid_indexes = [
                index for index, item in enumerate(page)
                if not isinstance(item, dict)
            ]
            if invalid_indexes:
                raise JQuantsError(
                    "J-Quants response contains invalid records: "
                    f"indexes={invalid_indexes}"
                )
            records.extend(page)
            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                return records
            query["pagination_key"] = str(pagination_key)

    def equities_master(self, *, code: str | None = None, date: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"code": code, "date": date}.items() if v}
        return self.fetch_all("equities/master", params)

    def financial_summaries(
        self, *, code: str | None = None, date: str | None = None
    ) -> list[dict[str, Any]]:
        """財務サマリーを取得する。

        `/fins/summary` が受け付けるのは `code` と `date` で、期間指定（from / to）は無い。
        銘柄を指定すると契約プランの範囲にある全開示が返り、開示日を指定するとその日の
        全銘柄の開示が1リクエストで返る。少なくとも一方の指定が必要。
        """
        params = {key: value for key, value in {"code": code, "date": date}.items() if value}
        if not params:
            raise JQuantsError("financial_summaries requires code or date")
        return self.fetch_all("fins/summary", params)

    def daily_prices(
        self,
        *,
        code: str | None = None,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """日次四本値を取得する。`code`と`date`は排他で、どちらか必須。

        `date`を指定すると**その日の全銘柄**が1リクエストで返る。銘柄単位で回すと
        リクエスト数が銘柄数に比例するが、日付単位なら日数にしか比例しない。
        全市場を対象にするときは`date`を使う。
        """
        if code is not None and date is not None:
            raise ValueError("daily_prices の code と date は同時に指定できません")
        if code is not None:
            params: dict[str, str] = {"code": code}
        elif date is not None:
            params = {"date": date}
        else:
            raise ValueError("daily_prices は code と date のどちらかが必要です")
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        return self.fetch_all("equities/bars/daily", params)
