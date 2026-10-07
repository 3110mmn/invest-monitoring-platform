"""分析層（Parquet）への接続をアプリの寿命で1つだけ持つ。

接続を都度張り直すと、そのたびにParquetのフッタとGCSの資格情報を読み直すため
1リクエストあたり2.8秒かかる（実測）。使い回せば0.15秒で済む。DuckDBのin-memory
接続はデータを保持しないので、使い回しても「第2の正本」にはならない。

`settings.parquet_lake` が未設定ならNoneのままで、価格と財務のAPIは503を返す。

viewの定義には所在をリテラルで埋めてあるため、`cursor()` で分けた別sessionからも
読める。以前は `SET VARIABLE` を参照しており、別sessionでは実体が
`read_parquet(NULL)` になって落ちた（DuckDBのUIで踏んだ）。

ただし同じ接続オブジェクトを複数スレッドから使うことは依然できない。result setが
別クエリのもので上書きされるため、`analytics_query_lock` で直列化している。
並列度を上げたくなったら、スレッドごとに `cursor()` を持たせる形にできる。
"""

from __future__ import annotations

import gc
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

import duckdb

from app.config import settings

logger = logging.getLogger(__name__)

_connection: duckdb.DuckDBPyConnection | None = None
_query_lock = RLock()


@contextmanager
def analytics_query_lock() -> Iterator[None]:
    """共有DuckDB接続へのクエリを直列化する。

    DuckDBの1接続は複数スレッドから同時に ``execute`` すると、別リクエストの
    result setで上書きされる。FastAPIは同期endpointをthread poolで実行するため、
    画面が価格と財務を並行取得するだけでこの競合が起きる。

    抜けるときに若い世代だけGCする。GCSを読むとfsspecのファイルオブジェクトが
    循環参照に入るため、放っておくとfsspecのIOスレッド側でGCされ、`__del__` の
    `sync()` が「running loopから呼ばれた」として例外を吐く。リクエストは成功するが
    ログがトレースバックで埋まる（実測で120リクエストあたり85回）。ここは
    ワーカースレッドでループが走っていないので、安全に回収できる。

    世代0に限るのは費用のため。全世代は48ms（1クエリ431msの11%）だが、世代0なら
    1.7msで済む。
    """
    with _query_lock:
        try:
            yield
        finally:
            gc.collect(0)


def open_analytics_connection() -> duckdb.DuckDBPyConnection | None:
    """起動時に接続を張って温める。繋げなければNoneを返す。

    起動時に一度読んでおくと、最初のリクエストが2.8秒待たされずに済む。

    **失敗してもアプリは起動させる。** 分析層へ繋がらないと価格は出せないが、テーマや
    監視対象の閲覧・編集は続けられる。ここで例外を投げるとコンテナが起動せず、
    価格が出ない程度の障害が全機能の停止に化ける。Parquetがまだ無い状態での初回
    デプロイもここに当たる。

    繋がらないことは黙らせない。`/ready` が `analytics: unavailable` を返し、価格と財務の
    エンドポイントは503になる。
    """
    global _connection
    if not settings.parquet_lake:
        logger.warning("PARQUET_LAKE が未設定です。価格と財務は提供できません")
        return None

    from analytics.runner import (
        FINANCIAL_DISCLOSURE_VIEW,
        PREFERRED_PRICE_VIEW,
        SECURITY_MASTER_VIEW,
        connect_lake,
    )

    try:
        connection = connect_lake(settings.parquet_lake)
        # フッタを読ませてキャッシュを温める。件数そのものに用は無い。価格と財務の
        # 両方を触るのは、片方だけ所在が違っていても起動時に気づけるようにするため。
        prices = connection.execute(f"SELECT COUNT(*) FROM {PREFERRED_PRICE_VIEW}").fetchone()
        disclosures = connection.execute(f"SELECT COUNT(*) FROM {FINANCIAL_DISCLOSURE_VIEW}").fetchone()
        securities = connection.execute(f"SELECT COUNT(*) FROM {SECURITY_MASTER_VIEW}").fetchone()
    except Exception:
        logger.exception(
            "分析層へ接続できません: %s。価格と財務は503になります",
            settings.parquet_lake,
        )
        return None

    _connection = connection
    logger.info(
        "分析層へ接続しました: %s（価格%s行 / 開示%s行 / 銘柄%s行）",
        settings.parquet_lake,
        f"{prices[0]:,}" if prices else "不明",
        f"{disclosures[0]:,}" if disclosures else "不明",
        f"{securities[0]:,}" if securities else "不明",
    )
    return _connection


def close_analytics_connection() -> None:
    """接続を閉じる。開いていなければ何もしない。"""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


def get_analytics_connection() -> duckdb.DuckDBPyConnection | None:
    """現在の接続を返す。分析層へ繋がっていなければNone。"""
    return _connection
