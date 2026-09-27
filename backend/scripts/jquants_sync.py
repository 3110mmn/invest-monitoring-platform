"""J-Quants Phase 1A market-data ingestion entry point."""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import Connection, connect_database
from app.etl.loaders import active_jquants_codes
from app.etl.normalizers import target_key_to_jpx_code
from app.etl.plan_window import PlanWindow, business_days, catch_up_range
from app.etl.runtime import build_jquants_pipeline


def years_ago(today: date, years: int) -> date:
    """指定年数だけ遡った日付を返す。うるう日は同年2月28日へ寄せる。"""
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def iso_date(value: str) -> str:
    """argparse向けにISO日付を検証し、正規化した文字列を返す。"""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"日付はYYYY-MM-DD形式で指定してください: {value}"
        ) from exc


def find_unmapped_jquants_codes(conn: Connection) -> list[str]:
    """jpx_codeの対応が無い有効な投資対象から、同期すべきJ-Quants Codeを導出する。

    対応済みの銘柄と、Codeを導出できない銘柄（日本株以外）は対象から除く。
    """
    rows = conn.execute(
        """
        SELECT a.target_key
        FROM investment_target a
        WHERE a.is_active = TRUE
          AND NOT EXISTS (
              SELECT 1
              FROM investment_target_identifier ai
              JOIN data_source ds ON ds.source_id = ai.source_id
              WHERE ai.target_id = a.target_id
                AND ds.source_key = 'jquants'
                AND ai.identifier_type = 'jpx_code'
                AND ai.valid_from <= CURRENT_DATE
                AND (ai.valid_to IS NULL OR ai.valid_to >= CURRENT_DATE)
          )
        ORDER BY a.target_key
        """
    ).fetchall()
    codes = [target_key_to_jpx_code(str(row["target_key"])) for row in rows]
    return [code for code in codes if code]


def sync_missing_masters(pipeline, conn: Connection, *, date: str | None = None) -> tuple[list[str], list[str]]:
    """jpx_code未対応の銘柄を同期し、(成功Code, 失敗Code) を返す。

    1銘柄の失敗で後続処理を止めない。識別子の対応は次回実行で作り直せるが、
    ここで異常終了すると価格取得と正本公開まで巻き添えで止まるため。
    失敗は `sync_master` が `ingestion_run` に status='failed' として記録する。
    """
    succeeded: list[str] = []
    failed: list[str] = []
    for code in find_unmapped_jquants_codes(conn):
        try:
            result = pipeline.sync_master(code=code, date=date)
            print(f"master sync: {code} -> {result}")
            succeeded.append(code)
        except Exception as exc:
            conn.rollback()
            failed.append(code)
            print(f"master sync failed, continuing: {code}: {exc}", file=sys.stderr)
    return succeeded, failed



def resolve_archive_dates(
    conn: Connection,
    args: argparse.Namespace,
    job_type: str,
    parser: argparse.ArgumentParser,
) -> list[str] | None:
    """アーカイブ対象の営業日を決める。取得済みなら None を返す。

    価格と財務で同じ規則にする。片方だけ窓の扱いが違うと、取りこぼしの原因になる。
    """
    window = PlanWindow(
        settings.jquants_history_lag_days, settings.jquants_history_years
    )
    today = date.today()

    if args.date:
        return [args.date]

    if args.catch_up:
        # 失敗した日を穴として残さない。前回の続きから最新日まで埋める。
        _, newest = window.bounds(today)
        row = conn.execute(
            """
            SELECT MAX(requested_to) AS last_date FROM ingestion_run
            WHERE job_type = ? AND status = 'succeeded'
            """,
            (job_type,),
        ).fetchone()
        last_archived = row["last_date"] if row else None
        pending = catch_up_range(last_archived, newest)
        if pending is None:
            print(f"アーカイブは最新です（{last_archived} まで取得済み）")
            return None
        dates = business_days(*pending, limit=args.max_days)
        print(f"前回: {last_archived} / 最新: {newest} → {len(dates)}営業日を取得")
        return dates

    # 既定はプランが提供する全期間。today から遡ると、窓は newest から2年なので
    # 最古側が取り漏れる。窓の端をそのまま使う。
    oldest, newest = window.bounds(today)
    end = date.fromisoformat(args.date_to) if args.date_to else newest
    start = date.fromisoformat(args.date_from) if args.date_from else oldest
    if start > end:
        parser.error("--fromは--to以前の日付を指定してください")
    clamped = window.clamp(start, end, today=today)
    if clamped is None:
        parser.error("指定した期間はプランの提供範囲と重なりません")
    start, end = clamped
    print(f"プランの窓に合わせた取得期間: {start} 〜 {end}")
    return business_days(start, end)


def main() -> None:
    parser = argparse.ArgumentParser(description="J-Quants銘柄マスタ・日次株価取込")
    subparsers = parser.add_subparsers(dest="command", required=True)

    master = subparsers.add_parser("master", help="銘柄マスタを同期")
    master.add_argument("--code", help="J-Quants Code。省略時は全銘柄")
    master.add_argument(
        "--missing",
        action="store_true",
        help="jpx_codeの対応が無い有効な投資対象だけを同期する",
    )
    master.add_argument("--date", type=iso_date, help="基準日 YYYY-MM-DD")

    financials = subparsers.add_parser("financials", help="財務サマリーを同期")
    financials_target = financials.add_mutually_exclusive_group()
    financials_target.add_argument(
        "--code", action="append", dest="codes", help="J-Quants Code。複数指定可"
    )
    financials_target.add_argument(
        "--date",
        type=iso_date,
        help="開示日 YYYY-MM-DD。その日の全銘柄の開示を1リクエストで取得する",
    )
    financials_target.add_argument(
        "--latest",
        action="store_true",
        help="契約プランで取得できる最新の開示日を同期する。日次実行向け",
    )

    prices = subparsers.add_parser(
        "prices", help="日次株価を同期。期間を指定しなければ直近1年を取得する"
    )
    prices.add_argument("--code", action="append", dest="codes", help="J-Quants Code。複数指定可")
    prices_period = prices.add_mutually_exclusive_group()
    prices_period.add_argument("--years", type=int, help="本日から遡る年数")
    prices_period.add_argument("--from", dest="date_from", type=iso_date, help="開始日 YYYY-MM-DD")
    prices.add_argument("--to", dest="date_to", type=iso_date, help="終了日 YYYY-MM-DD（省略時は本日）")

    archive = subparsers.add_parser(
        "archive-prices",
        help="全銘柄の日次四本値をrawへ保存する。PostgreSQLへはロードしない",
    )
    archive_period = archive.add_mutually_exclusive_group()
    archive_period.add_argument("--date", help="単日 YYYY-MM-DD")
    archive_period.add_argument("--all", action="store_true", help="プランが提供する全期間")
    archive_period.add_argument(
        "--catch-up",
        action="store_true",
        help="前回アーカイブ済みの翌日から、プランが提供する最新日まで追いつく（日次用）",
    )
    archive.add_argument(
        "--max-days", type=int, default=30, help="--catch-up で一度に取得する最大日数"
    )
    archive.add_argument("--from", dest="date_from", type=iso_date, help="開始日 YYYY-MM-DD")
    archive.add_argument("--to", dest="date_to", type=iso_date, help="終了日 YYYY-MM-DD")

    archive_fin = subparsers.add_parser(
        "archive-financials",
        help="全銘柄の財務サマリーをrawへ保存する。PostgreSQLへはロードしない",
    )
    archive_fin_period = archive_fin.add_mutually_exclusive_group()
    archive_fin_period.add_argument("--date", help="単日 YYYY-MM-DD")
    archive_fin_period.add_argument("--all", action="store_true", help="プランが提供する全期間")
    archive_fin_period.add_argument(
        "--catch-up", action="store_true", help="前回アーカイブ済みの翌日から追いつく（日次用）"
    )
    archive_fin.add_argument("--from", dest="date_from", type=iso_date, help="開始日")
    archive_fin.add_argument("--to", dest="date_to", type=iso_date, help="終了日")
    archive_fin.add_argument(
        "--max-days", type=int, default=30, help="--catch-up で一度に取得する最大日数"
    )

    args = parser.parse_args()
    if args.command == "prices":
        if args.years is not None and args.years < 1:
            parser.error("--yearsは1以上を指定してください")
        if (
            args.date_from is not None
            and args.date_to is not None
            and args.date_from > args.date_to
        ):
            parser.error("--fromは--to以前の日付を指定してください")

    conn = connect_database(read_only=False)
    try:
        pipeline = build_jquants_pipeline(conn)
        if args.command == "master":
            if args.missing:
                succeeded, failed = sync_missing_masters(pipeline, conn, date=args.date)
                if not succeeded and not failed:
                    print("jpx_code未対応の投資対象はありません")
                if failed:
                    # 異常終了させない。失敗Codeは次回実行で再試行される。
                    print(
                        f"銘柄マスタ同期に失敗したCode（次回実行で再試行）: {', '.join(failed)}",
                        file=sys.stderr,
                    )
                return
            result = pipeline.sync_master(code=args.code, date=args.date)
        elif args.command == "archive-prices":
            dates = resolve_archive_dates(
                conn, args, "jquants_market_prices_archive_v1", parser
            )
            if dates is None:
                return
            print(f"対象営業日（概算）: {len(dates)}日")
            result = pipeline.archive_market_prices(dates)
        elif args.command == "archive-financials":
            dates = resolve_archive_dates(
                conn, args, "jquants_financial_archive_v1", parser
            )
            if dates is None:
                return
            print(f"対象営業日（概算）: {len(dates)}日")
            result = pipeline.archive_financials(dates)
        elif args.command == "financials":
            if args.latest:
                # プランが提供する最新日。遅延のないプランでは当日になる。
                _, latest = PlanWindow(
                    settings.jquants_history_lag_days, settings.jquants_history_years
                ).bounds(date.today())
                print(f"取得可能な最新の開示日: {latest}")
                result = pipeline.sync_financials(date=latest.isoformat())
            elif args.date:
                result = pipeline.sync_financials(date=args.date)
            else:
                codes = args.codes or active_jquants_codes(conn)
                if not codes:
                    parser.error("--codeか--dateを指定するか、先にmasterを同期してください")
                result = pipeline.sync_financials(codes=codes)
        else:
            codes = args.codes or active_jquants_codes(conn)
            if not codes:
                parser.error("--codeを指定するか、先にmasterを同期してください")
            end = date.fromisoformat(args.date_to) if args.date_to else date.today()
            start = (
                date.fromisoformat(args.date_from)
                if args.date_from
                else years_ago(end, args.years or 1)
            )
            if start > end:
                parser.error("開始日は終了日以前を指定してください")
            # プランの提供範囲外を含むリクエストは全体が拒否されるため、先に収める。
            window = PlanWindow(
                settings.jquants_history_lag_days, settings.jquants_history_years
            )
            clamped = window.clamp(start, end, today=date.today())
            if clamped is None:
                earliest, latest = window.bounds(date.today())
                parser.error(
                    f"要求期間 {start}〜{end} はプランの提供範囲 {earliest}〜{latest} と重なりません"
                )
            if clamped != (start, end):
                print(f"プランの提供範囲に合わせて {clamped[0]}〜{clamped[1]} へ調整しました")
            result = pipeline.sync_prices(
                codes=codes,
                date_from=clamped[0].isoformat(),
                date_to=clamped[1].isoformat(),
            )
        print(result)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
