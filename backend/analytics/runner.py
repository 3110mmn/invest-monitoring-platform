"""Derivedの計算定義（SQL）をDuckDBで実行する。

**計算定義が正本であり、結果は派生である。** ここはSQLを読んで実行するだけで、
結果をどこかへ固定する役割は持たない。materializeが必要になるのは、高コスト・複数用途で
共有・過去に提示した判断のEvidence、のいずれかが成立したときで、それまでは都度計算する。

SQLをPythonの文字列に埋めず`.sql`として置くのは、定義そのものをレビューでき、将来dbtへ
移すときに同じファイルを使えるようにするため。
"""

from __future__ import annotations

import atexit
import gc
import os
from pathlib import Path

import duckdb

DERIVED_DIR = Path(__file__).parent / "derived"

# 分析層のデータ所在。ローカルとGCSのどちらも `read_parquet` がそのまま読む。
DEFAULT_PARQUET_GLOB = "data/parquet/observed/market_price/**/*.parquet"

GCS_SCHEMES = ("gs://", "gcs://")

# 「同じ日に複数の取得元があるときどれを採るか」の唯一の定義。接続時にviewとして張り、
# Derivedの計算もAPIの読み出しもここを通す。両者が別々に優先順位を持つと、画面に出る
# 終値と計算されたリターンが食い違う。
PREFERRED_PRICE_VIEW = "preferred_price"

# 開示をそのまま見せるview。価格と違い、ここに採用の規則は無い。訂正開示は開示番号が
# 別なので畳まず、どれが最新かは利用側が決める。
FINANCIAL_DISCLOSURE_VIEW = "financial_disclosure"

# lake配下の配置。ビルダーの --publish と同じ単位で揃える。
PRICE_SUBPATH = "observed/market_price"
FINANCIAL_SUBPATH = "observed/financial_summary"

_shutdown_hook_registered = False


def load_definition(name: str) -> str:
    """Derivedの計算定義を読む。名前は拡張子なしのファイル名。"""
    path = DERIVED_DIR / f"{name}.sql"
    if not path.is_file():
        available = sorted(p.stem for p in DERIVED_DIR.glob("*.sql"))
        raise FileNotFoundError(f"計算定義がありません: {name}（利用可能: {', '.join(available)}）")
    return path.read_text(encoding="utf-8")


def _ensure_ca_bundle() -> None:
    """`SSL_CERT_FILE` が未設定なら certifi のCA束を指す。

    gcsfsはaiohttp経由でHTTPSを張るが、aiohttpはOpenSSLの既定パスしか見ない。
    python.org配布のPythonはそこにCA束を持たないため、証明書検証が落ちる。
    Dockerイメージやマネージド環境ではOSのCA束が入っているので、この分岐は通らない。
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


def _release_handles_before_shutdown() -> None:
    """インタプリタ終了前に、GCS上のファイルハンドルを回収させる。

    DuckDBが開いたfsspecのファイルオブジェクトは循環参照に入るため、参照が切れても
    すぐには解放されない。インタプリタ終了時まで残ると、その時点では既にfsspecのIO
    ループが止まっているのに `__del__` が `sync()` で待ちに入り、**プロセスが終了
    しなくなる**（実測で60秒待っても終わらなかった）。

    `atexit` はデーモンスレッドが落ちる前に走るので、ここでGCを回せばループが生きて
    いるうちに解放できる。同じ対処で終了が3秒に戻ることを実測で確認している。
    """
    global _shutdown_hook_registered
    if _shutdown_hook_registered:
        return
    atexit.register(gc.collect)
    _shutdown_hook_registered = True


def _disable_adaptive_prefetching() -> None:
    """gcsfsの適応的prefetchを切る。

    実験的機能だが既定で有効で、読み出しごとに背景タスクを持つファイルを作る。DuckDBは
    それを速やかに閉じないため、fsspecのIOスレッド側で回収されて `__del__` が例外を吐き、
    asyncioも「Task was destroyed but it is pending」を出し続ける。処理は成功している
    のにログがトレースバックで埋まり、本物のエラーが埋もれる。

    切っても速度は変わらない（実測で中央値140ms→145ms、誤差の範囲）。gcsfs自身も
    「100MB未満の読み出しには効かない」と書いており、ここのParquetは1ファイル
    1〜10MBである。

    **環境変数で切る。** この判定はファイルを開くたびに `os.environ` を読むので、
    `fsspec.filesystem()` へkwargで渡しても届かない。利用者が明示していれば尊重する。
    """
    os.environ.setdefault("USE_EXPERIMENTAL_ADAPTIVE_PREFETCHING", "false")


def _register_gcs(connection: duckdb.DuckDBPyConnection) -> None:
    """GCSをfsspec（gcsfs）としてDuckDBへ差し込む。

    DuckDB本体のhttpfsはGCSをS3互換エンドポイントとして扱うため、ADCではなく
    **HMACキー**を要求する。鍵をもう1つ管理したくないので、Pythonクライアントの
    `register_filesystem` でgcsfsに委譲する。gcsfsはADCをそのまま解決するので、
    ローカルでは`gcloud auth application-default login`、Cloud Runでは
    メタデータサーバの資格情報が、追加設定なしで効く。

    `gs://` と `gcs://` はどちらもgcsfsが受け持つため、URIの書き換えは不要。
    """
    try:
        import fsspec
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向けの案内
        raise RuntimeError(
            "GCS上のParquetを読むには gcsfs が必要です: "
            "pip install -r backend/requirements-analytics.txt"
        ) from exc
    _ensure_ca_bundle()
    _release_handles_before_shutdown()
    _disable_adaptive_prefetching()
    connection.register_filesystem(fsspec.filesystem("gcs"))


def open_connection(parquet_glob: str) -> duckdb.DuckDBPyConnection:
    """入力の所在だけを束縛したDuckDB接続を返す。viewは張らない。

    in-memoryで十分で、`.duckdb`ファイルは作らない。データはParquetにあり、DuckDBは
    計算エンジンとして使う。ファイルを作ると、それが第2の正本に見えてしまう。

    所在がGCSならgcsfsを差し込む。ローカルパスならそのまま読むので、テストと
    手元の確認は外部依存なしで動く。

    価格以外のParquet（財務など）を読むときはこちらを使う。`connect` は価格のviewを
    張るため、列の揃わないParquetでは失敗する。
    """
    connection = duckdb.connect()
    if parquet_glob.startswith(GCS_SCHEMES):
        _register_gcs(connection)
    connection.execute("SET VARIABLE parquet_glob = ?", [parquet_glob])
    return connection


def connect(parquet_glob: str = DEFAULT_PARQUET_GLOB) -> duckdb.DuckDBPyConnection:
    """価格を読む接続を返す。`preferred_price` viewを張る。

    採用する観測の選び方をviewへ寄せてあるので、価格を読む経路は必ずここを通す。
    """
    connection = open_connection(parquet_glob)
    connection.execute(
        f"CREATE OR REPLACE VIEW {PREFERRED_PRICE_VIEW} AS "
        f"{build_query(load_definition(PREFERRED_PRICE_VIEW), glob=parquet_glob)}"
    )
    return connection


def sql_literal(value: str) -> str:
    """文字列をSQLのリテラルにする。パスに含まれる `'` を潰す。"""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def lake_glob(lake: str, subpath: str) -> str:
    """lakeのルートから、そのデータセットのglobを組む。"""
    return f"{lake.rstrip('/')}/{subpath}/**/*.parquet"


def connect_lake(lake: str) -> duckdb.DuckDBPyConnection:
    """価格と財務の両方を読む接続を返す。APIが使う入口。

    1つの接続で両方を扱う。接続を2つ持つと、温めるコストも終了処理も二重になる。

    **viewの定義にはパスをリテラルで埋める。** session変数（`SET VARIABLE`）を参照すると、
    viewはcatalogを共有する別sessionから見えるのに実体が `read_parquet(NULL)` になり、
    `read_parquet cannot take NULL list as parameter` で落ちる。DuckDBのUIや `cursor()`
    はいずれも別sessionなので、これを踏む。リテラルなら誰が開いても同じ結果になる。
    """
    price_glob = lake_glob(lake, PRICE_SUBPATH)
    financial_glob = lake_glob(lake, FINANCIAL_SUBPATH)
    connection = open_connection(price_glob)
    connection.execute(
        f"CREATE OR REPLACE VIEW {PREFERRED_PRICE_VIEW} AS "
        f"{build_query(load_definition(PREFERRED_PRICE_VIEW), glob=price_glob)}"
    )
    connection.execute(
        f"CREATE OR REPLACE VIEW {FINANCIAL_DISCLOSURE_VIEW} AS SELECT * FROM "
        f"read_parquet({sql_literal(financial_glob)}, hive_partitioning = true)"
    )
    return connection


def build_query(definition: str, *, glob: str | None = None) -> str:
    """定義中のプレースホルダを、入力の所在へ置き換える。

    `glob` を渡すとリテラルを埋める。viewの定義に使う場合は必ずこちらにする。
    session変数のままだと、別sessionからviewを開いたときに実体がNULLになる。
    """
    # targetを指定しない通常のDerived実行では全銘柄を対象にする。APIは必要な銘柄だけを
    # window計算前に絞るため、repository側でこのplaceholderを明示的に置換する。
    definition = definition.replace("$target_filter", "")
    if glob is not None:
        return definition.replace("$parquet_glob", sql_literal(glob))
    return definition.replace("$parquet_glob", "getvariable('parquet_glob')")


def run(
    name: str,
    *,
    parquet_glob: str = DEFAULT_PARQUET_GLOB,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> duckdb.DuckDBPyRelation:
    """Derivedを計算してrelationを返す。呼び出し側がさらに絞り込める。"""
    con = connection or connect(parquet_glob)
    return con.sql(build_query(load_definition(name)))
