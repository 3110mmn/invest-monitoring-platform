"""日次の価格取得。

監視対象の直近価格をyfinanceから取り、**rawとして残すところまで**を担う。
PostgreSQLへは価格を入れない。価格の所在は分析層（Parquet）であり、PostgreSQLは
「何を監視しているか」だけを持つ。

この経路が埋めるのは、J-Quantsの契約プランが提供しない直近84日である。全市場の
履歴は `jquants_sync.py archive-prices` が日付単位でrawへ蓄積する。

使い方:
    python backend/scripts/daily_update.py
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.config import settings
from app.database import connect_database
from app.etl.lineage import display_path, finish_run, stage_raw, start_run
from app.etl.loaders import ensure_data_source

DEFAULT_LOOKBACK_DAYS = 7
REFETCH_OVERLAP_DAYS = 3

YFINANCE_SOURCE_KEY = "yfinance"


def latest_observations(lake: str | None, source_key: str) -> dict[str, date]:
    """分析層から、銘柄ごとの最終観測日を引く。

    **「どこまで取得したか」はParquetが答える。** 以前は
    `market_price_observation` の最終観測日を見ていたが、価格をPostgreSQLへ入れなく
    なったため、その記録は分析層にしかない。

    取得元で絞るのは、yfinanceの再開位置を決めるのにJ-Quantsの観測を混ぜないため。
    混ぜると、84日前までJ-Quantsが埋めている銘柄で「もう取得済み」と誤判定する。

    lakeへ繋がらなければ空を返す。呼び出し側は既定の遡及日数へ倒すので、取得が
    止まるより取りすぎる方へ倒れる。
    """
    if not lake:
        return {}
    try:
        from analytics.runner import connect_lake

        connection = connect_lake(lake)
    except Exception as exc:
        print(f"  ! 分析層へ繋がりません（{str(exc)[:80]}）。既定の遡及日数で取得します")
        return {}
    try:
        rows = connection.execute(
            "SELECT target_key, MAX(obs_date) FROM "
            "read_parquet(getvariable('parquet_glob'), hive_partitioning = true) "
            "WHERE source_key = ? AND target_key IS NOT NULL GROUP BY 1",
            [source_key],
        ).fetchall()
    except Exception as exc:
        print(f"  ! 最終観測日を読めません（{str(exc)[:80]}）。既定の遡及日数で取得します")
        return {}
    finally:
        connection.close()
    return {str(key): latest for key, latest in rows if latest is not None}


def resolve_fetch_start(
    latest_by_key: dict[str, date],
    target_key: str,
    *,
    today: date,
    overlap_days: int = REFETCH_OVERLAP_DAYS,
    default_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> date:
    """銘柄ごとの最終観測日から、再取得の開始日を決める。

    固定窓では実行が飛んだ期間の穴が埋まらないため、最終観測日を起点にする。
    直近数日は訂正や確定遅れがあるため `overlap_days` だけ遡って取り直す。
    観測がまだ無い銘柄は既定の遡及日数しか遡らない。初期投入は日次更新ではなく
    バックフィルの役割とし、ここで長期間を取りにいかない。
    """
    latest = latest_by_key.get(target_key)
    if latest is None:
        print(
            f"  ! {target_key} はこの取得元での観測が無いため"
            f"直近{default_lookback_days}日のみ取得します"
        )
        return today - timedelta(days=default_lookback_days)
    return latest - timedelta(days=overlap_days)


def frame_to_records(target_key: str, frame) -> list[dict[str, Any]]:
    """yfinanceの取得結果を、rawとして保存できる形へ写す。

    yfinanceはHTTPレスポンスそのものを返さないため、ライブラリが解釈した後の値が
    保存できる最も原本に近い成果物になる。再現できる範囲を正直に保つため、
    加工せずそのまま写す。
    """
    records: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        records.append(
            {
                "target_key": target_key,
                "obs_date": index.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in frame.columns else None,
            }
        )
    return records


def update_investment_target_prices(since: date | None = None) -> None:
    """監視対象の価格をrawへ蓄積する。

    `since` を渡すと、最終観測日ではなくその日から取得する。取りこぼした期間の
    rawを作り直すときに使う。
    """
    conn = connect_database(read_only=False)
    source_id = ensure_data_source(conn, "yfinance", "Yahoo Finance via yfinance")

    targets = [
        (str(row["target_key"]), str(row["target_name"]))
        for row in conn.execute(
            """SELECT t.target_key, t.target_name FROM investment_target t
               WHERE EXISTS (SELECT 1 FROM watchlist_entry w
                             WHERE w.target_id = t.target_id AND w.status = 'monitoring')
                  OR EXISTS (
                      SELECT 1 FROM mandate_target_assignment a
                      JOIN mandate_version v ON v.mandate_version_id = a.mandate_version_id
                      JOIN capital_allocation_mandate m ON m.mandate_id = v.mandate_id
                      WHERE a.target_id = t.target_id AND a.status = 'active'
                        AND v.effective_until IS NULL AND m.status = 'active'
                  )
               ORDER BY t.target_id"""
        ).fetchall()
    ]
    print(f"\n=== 価格取得 ({len(targets)}件) ===\n")

    latest_by_key = {} if since else latest_observations(
        settings.parquet_lake, YFINANCE_SOURCE_KEY
    )

    # 取得したものは必ずrawとして残す。ここが欠けると、調整済み価格が分割で遡及して
    # 変わったとき「当時いくらで見えていたか」を再現できない。
    job_type = "yfinance_daily_prices_v1"
    project_root = Path(__file__).resolve().parents[2]
    run_id = start_run(
        conn, source_id, job_type, project_root, target_count=len(targets)
    )
    raw_records: list[dict[str, Any]] = []
    success = error = 0
    today = date.today()
    for target_key, target_name in targets:
        try:
            start = since or resolve_fetch_start(latest_by_key, target_key, today=today)
            frame = yf.Ticker(target_key).history(
                start=start, end=today + timedelta(days=1), auto_adjust=True
            )
            if frame.empty:
                print(f"  - {target_name}: {start} 以降の取得結果なし")
                error += 1
                continue

            records = frame_to_records(target_key, frame)
            raw_records.extend(records)
            latest = frame.index[-1].strftime("%Y-%m-%d")
            close = float(frame["Close"].iloc[-1])
            print(f"  ✓ {target_name} [{start}〜{latest}] {len(records)}件 C:{close:.2f}")
            success += 1

        except Exception as exc:
            print(f"  ✗ {target_name}: {exc}")
            error += 1

    # 1銘柄でも取得できたならrawを残す。全滅したときだけraw無しで失敗を記録する。
    raw_path = None
    if raw_records:
        raw_path = display_path(
            stage_raw(settings.raw_data_dir, "yfinance", job_type, run_id, raw_records),
            project_root,
        )
    finish_run(
        conn,
        run_id,
        status="succeeded" if error == 0 else ("failed" if success == 0 else "partial"),
        fetched=len(raw_records),
        loaded=0,
        skipped=0,
        failed=error,
        raw_path=raw_path,
    )

    conn.commit()
    conn.close()
    print(f"\n完了: 成功={success} 失敗={error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="監視対象の価格をrawへ蓄積する")
    parser.add_argument(
        "--since",
        type=lambda v: date.fromisoformat(v),
        help="この日から取得する（省略時は最終観測日から増分取得）",
    )
    options = parser.parse_args()

    print("=" * 60)
    print("投資監視システム - 日次データ更新")
    print("=" * 60)

    update_investment_target_prices(since=options.since)

    print("\n" + "=" * 60 + "\nデータ更新完了\n" + "=" * 60)
