"""
日次データ更新スクリプト
アクティブ投資対象の価格 (market_price_observation) を更新する。

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
from app.database import Connection, connect_database
from app.etl.lineage import display_path, finish_run, stage_raw, start_run
from app.etl.loaders import ensure_data_source
from app.etl.runtime import build_jquants_pipeline


def partition_targets_by_price_source(
    investment_targets: list[tuple[int, str, str, str | None]],
    *,
    jquants_enabled: bool,
) -> tuple[list[str], list[tuple[int, str, str]]]:
    """J-Quants対応銘柄とyfinance対象銘柄を重複なく分ける。"""
    if not jquants_enabled:
        return [], [
            (target_id, target_key, target_name)
            for target_id, target_key, target_name, _ in investment_targets
        ]
    return (
        [jpx_code for _, _, _, jpx_code in investment_targets if jpx_code],
        [
            (target_id, target_key, target_name)
            for target_id, target_key, target_name, jpx_code in investment_targets
            if not jpx_code
        ],
    )


DEFAULT_LOOKBACK_DAYS = 7
REFETCH_OVERLAP_DAYS = 3


def resolve_fetch_start(
    conn: Connection,
    target_id: int,
    source_id: int,
    *,
    today: date,
    overlap_days: int = REFETCH_OVERLAP_DAYS,
    default_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> date:
    """銘柄・取得元ごとの最終観測日から、再取得の開始日を決める。

    固定窓では実行が飛んだ期間の穴が埋まらないため、最終観測日を起点にする。
    直近数日は訂正や確定遅れがあるため `overlap_days` だけ遡って取り直す。
    その取得元での観測がまだ無い銘柄は既定の遡及日数しか遡らない。取得元ごとの初期投入は
    日次更新ではなくバックフィルの役割とし、ここで長期間を取りにいかない。
    """
    row = conn.execute(
        "SELECT max(obs_date) AS latest_obs_date FROM market_price_observation "
        "WHERE target_id = ? AND source_id = ?",
        (target_id, source_id),
    ).fetchone()
    if not row or not row["latest_obs_date"]:
        print(
            f"  ! target_id={target_id} はこの取得元での観測が無いため直近{default_lookback_days}日のみ取得します"
            "（過去分が必要ならバックフィルを実行してください）"
        )
        return today - timedelta(days=default_lookback_days)
    latest = row["latest_obs_date"]
    return (latest if isinstance(latest, date) else date.fromisoformat(str(latest))) - timedelta(
        days=overlap_days
    )


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


def upsert_price_records(
    conn: Connection, target_id: int, source_id: int, run_id: int, records: list[dict[str, Any]]
) -> int:
    """1行も捨てずにUPSERTし、保存した行数を返す。どの実行で入ったかを残す。"""
    saved = 0
    for record in records:
        conn.execute(
            """
            INSERT INTO market_price_observation (
                target_id, source_id, ingestion_run_id, obs_date, open_price, high_price,
                low_price, close_price, volume, price_basis, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'adjusted', CURRENT_TIMESTAMP)
            ON CONFLICT(target_id, source_id, obs_date) DO UPDATE SET
                ingestion_run_id = excluded.ingestion_run_id,
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                close_price = excluded.close_price,
                volume = excluded.volume,
                price_basis = excluded.price_basis,
                fetched_at = excluded.fetched_at
            """,
            (
                target_id,
                source_id,
                run_id,
                record["obs_date"],
                record["open"],
                record["high"],
                record["low"],
                record["close"],
                record["volume"],
            ),
        )
        saved += 1
    return saved


def update_investment_target_prices(since: date | None = None) -> None:
    """アクティブ投資対象の価格を更新する。

    `since` を渡すと、最終観測日ではなくその日から取得する。rawを残し始める前に
    PostgreSQLへ入れた期間は、通常の増分取得では取りに行かないため、分析層へ
    渡すrawを作り直すときに使う。
    """
    conn = connect_database(read_only=False)
    cursor = conn.cursor()
    yfinance_source_id = ensure_data_source(
        conn,
        "yfinance",
        "Yahoo Finance via yfinance",
    )

    cursor.execute("""
        SELECT a.target_id, a.target_key, a.target_name,
               (
                   SELECT ai.identifier
                   FROM investment_target_identifier ai
                   JOIN data_source ds ON ds.source_id = ai.source_id
                   WHERE ai.target_id = a.target_id
                     AND ds.source_key = 'jquants'
                     AND ai.identifier_type = 'jpx_code'
                     AND ai.is_primary = TRUE
                     AND ai.valid_from <= CURRENT_DATE
                     AND (ai.valid_to IS NULL OR ai.valid_to >= CURRENT_DATE)
                   ORDER BY ai.valid_from DESC
                   LIMIT 1
               ) AS jpx_code
        FROM investment_target a
        WHERE a.is_active = TRUE
        ORDER BY a.target_id
    """)
    investment_targets = [
        (
            int(row["target_id"]),
            str(row["target_key"]),
            str(row["target_name"]),
            str(row["jpx_code"]) if row["jpx_code"] is not None else None,
        )
        for row in cursor.fetchall()
    ]

    # 対応(jpx_code)があっても、日次取得に使うかは設定で明示する。プランの提供期間外だと
    # J-Quants経路へ振り分けた銘柄がどこからも取得できなくなるため。
    jquants_daily = bool(settings.jquants_api_key) and settings.jquants_daily_enabled
    jquants_codes, yfinance_targets = partition_targets_by_price_source(
        investment_targets, jquants_enabled=jquants_daily
    )

    if settings.jquants_api_key and not settings.jquants_daily_enabled:
        print("J-Quantsの日次取得は無効（JQUANTS_DAILY_ENABLED=false）。yfinanceで取得します")

    if jquants_daily:
        if jquants_codes:
            jquants_source_id = ensure_data_source(conn, "jquants", "J-Quants")
            # 対象銘柄で最も古い開始日に合わせ、実行が飛んだ期間の穴も埋める。
            start = min(
                resolve_fetch_start(conn, target_id, jquants_source_id, today=date.today())
                for target_id, _, _, jpx_code in investment_targets
                if jpx_code
            )
            try:
                result = build_jquants_pipeline(conn).sync_prices(
                    codes=jquants_codes,
                    date_from=start.strftime("%Y-%m-%d"),
                    date_to=date.today().strftime("%Y-%m-%d"),
                )
                print(f"J-Quants価格更新: {result}")
            except Exception as exc:
                conn.rollback()
                print(f"J-Quants価格更新に失敗: {exc}")
        if not jquants_codes:
            print("J-Quantsの日次取得は有効だが、jpx_codeの対応がある有効銘柄がありません")
    print(f"\n=== yfinance対象銘柄の価格取得 ({len(yfinance_targets)}件) ===\n")

    # 取得したものは必ずrawとして残す。ここが欠けると、調整済み価格が分割で遡及して
    # 変わったとき「当時いくらで見えていたか」を再現できない。
    job_type = "yfinance_daily_prices_v1"
    project_root = Path(__file__).resolve().parents[2]
    run_id = start_run(
        conn, yfinance_source_id, job_type, project_root, target_count=len(yfinance_targets)
    )
    raw_records: list[dict[str, Any]] = []
    success = error = 0
    loaded = 0
    today = date.today()
    for target_id, target_key, target_name in yfinance_targets:
        try:
            start = since or resolve_fetch_start(
                conn, target_id, yfinance_source_id, today=today
            )
            df = yf.Ticker(target_key).history(
                start=start, end=today + timedelta(days=1), auto_adjust=True
            )
            if df.empty:
                print(f"  - {target_name}: {start} 以降の取得結果なし")
                error += 1
                continue

            records = frame_to_records(target_key, df)
            raw_records.extend(records)
            saved = upsert_price_records(conn, target_id, yfinance_source_id, run_id, records)
            loaded += saved
            latest = df.index[-1].strftime("%Y-%m-%d")
            print(f"  ✓ {target_name} [{start}〜{latest}] {saved}件 C:{float(df['Close'].iloc[-1]):.2f}")
            success += 1

        except Exception as e:
            print(f"  ✗ {target_name}: {e}")
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
        loaded=loaded,
        skipped=0,
        failed=error,
        raw_path=raw_path,
    )

    conn.commit()
    conn.close()
    print(f"\n完了: 成功={success} 失敗={error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="アクティブ投資対象の価格を更新する")
    parser.add_argument(
        "--since",
        type=lambda v: date.fromisoformat(v),
        help="この日から取得する（省略時は最終観測日から増分取得）",
    )
    options = parser.parse_args()

    print("=" * 60)
    print("投資監視システム - 日次データ更新")
    print("=" * 60)

    print("\n=== アクティブ投資対象の価格取得 ===")
    update_investment_target_prices(since=options.since)

    print("\n" + "=" * 60 + "\nデータ更新完了\n" + "=" * 60)
