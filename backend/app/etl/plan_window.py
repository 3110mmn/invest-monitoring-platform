"""データ提供プランの取得可能期間に、要求期間を収める。

J-Quantsは契約プランの範囲外を含むリクエストを部分的に返さず、リクエスト全体を
HTTP 400 で拒否する。実際のレスポンス例:

    Your subscription covers the following dates: 2024-06-20 ~ 2026-06-20

そのため、要求期間をプランの窓へあらかじめ収めてから送信する。
"""

from datetime import date, timedelta


class PlanWindow:
    """プランが提供する期間の窓。

    Attributes:
        lag_days: 直近この日数分は提供されない（Freeは84日=12週）。
        history_years: 遡れる年数（Freeは2年）。
    """

    def __init__(self, lag_days: int, history_years: int) -> None:
        self.lag_days = lag_days
        self.history_years = history_years

    def bounds(self, today: date) -> tuple[date, date]:
        """指定日時点で取得できる最古日と最新日を返す。"""
        latest = today - timedelta(days=self.lag_days)
        try:
            earliest = latest.replace(year=latest.year - self.history_years)
        except ValueError:  # 2月29日
            earliest = latest.replace(year=latest.year - self.history_years, day=28)
        return earliest, latest

    def clamp(self, date_from: date, date_to: date, *, today: date) -> tuple[date, date] | None:
        """要求期間を窓へ収める。重なりが無ければ None を返す。"""
        earliest, latest = self.bounds(today)
        start = max(date_from, earliest)
        end = min(date_to, latest)
        if start > end:
            return None
        return start, end

def business_days(start: date, end: date, *, limit: int | None = None) -> list[str]:
    """start〜endの平日をISO文字列で返す。休場日はAPIが空を返すので除外しない。

    土日は市場が開かないと確定しているため投げない。祝日はカレンダーを持たないと
    判別できず、空振り1回のコストよりカレンダー管理のコストが上回るため許容する。
    """
    days = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    ]
    return days[:limit] if limit is not None else days


def catch_up_range(
    last_archived: date | None, newest_available: date
) -> tuple[date, date] | None:
    """前回アーカイブ済みの翌日から、取得可能な最新日までを返す。

    追いつくべき日が無ければNone。失敗した日を穴として残さないため、日次実行は
    「最新日だけ」ではなく「前回の続きから」取得する。
    """
    start = last_archived + timedelta(days=1) if last_archived else newest_available
    if start > newest_available:
        return None
    return start, newest_available
