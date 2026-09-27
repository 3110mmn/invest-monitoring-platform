"""rawレスポンスから分析用のtyped Parquetを組み立てる。

**rawが原本であり、Parquetは派生**である。いつでも作り直せる状態を維持することが要件で、
Parquetを正本として扱わない。

rawの所在は `ingestion_run.raw_path` から引く。filesystemを直接globすると、GCSへ公開済みの
rawを取りこぼす。またGitHub Actionsのランナーは使い捨てで、ローカルにはその実行で取得した
分しか残らないため、2年分を横断するにはDBの索引が要る。

正規化はPostgreSQLへのロードと同じ `normalizers` を使う。ここで別の変換を書くと、
同じ「価格」がPostgreSQLとParquetで食い違う。

Parquetには**business key**（`target_key`、`source_key`）を持たせ、PostgreSQLの
surrogate key（`target_id`、`source_id`）を書かない。内部IDだけを持つと、PostgreSQLへ
joinしないと何のデータか分からず、分析層がServing DBへ依存してしまう。

使い方:
    python backend/scripts/build_parquet.py --out data/parquet
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.base_parquet import load_base_rows
from app.config import settings
from app.database import connect_database
from app.etl.normalizers import normalize_jquants_price
from app.etl.raw_store import iter_raw

# J-Quantsコードは5桁で、末尾の0を落とすと内部のtarget_keyになる（72030 → 7203.T）。
JQUANTS_SOURCE_KEY = "jquants"

PRICE_SCHEMA = pa.schema(
    [
        ("target_key", pa.string()),
        ("jpx_code", pa.string()),
        ("source_key", pa.string()),
        ("obs_date", pa.date32()),
        ("open_price", pa.float64()),
        ("high_price", pa.float64()),
        ("low_price", pa.float64()),
        ("close_price", pa.float64()),
        ("volume", pa.float64()),
        ("price_basis", pa.string()),
        ("ingestion_run_id", pa.int64()),
        ("built_at", pa.timestamp("us", tz="UTC")),
    ]
)


def jpx_code_to_target_key(jpx_code: str) -> str | None:
    """J-Quants Codeを内部のtarget_keyへ写す（`72030` → `7203.T`）。

    末尾が0でない5桁コードは優先株など普通株以外の証券で、`.T` ティッカーへ一意に
    写せない。推測せずNoneを返す。**行は落とさない。** `jpx_code` 自体がbusiness keyとして
    残るため、target_keyが無くても何のデータかは分かる。写せないことを理由に一次観測を
    捨てると、実在する証券の価格が分析から消える。
    """
    code = jpx_code.strip()
    if len(code) != 5 or not code.endswith("0"):
        return None
    return f"{code[:4]}.T"


def target_key_to_jpx_code(target_key: str) -> str | None:
    """内部のtarget_keyをJ-Quants Codeへ写す（`7203.T` → `72030`）。

    yfinance由来の行にもjpx_codeを入れて、J-Quants由来と同じキーで突き合わせられる
    ようにする。米国ETFなど `.T` で終わらないものは日本の証券コードを持たないのでNone。
    """
    if not target_key.endswith(".T"):
        return None
    base = target_key[:-2]
    return f"{base}0" if len(base) == 4 else None


def build_yfinance_rows(
    records: list[dict[str, Any]], run_id: int
) -> tuple[list[dict[str, Any]], int, int]:
    """yfinanceのrawをParquet用の行へ変換する。

    J-Quantsとスキーマを揃え、`source_key` で取得元を区別する。同じ日に両方の観測が
    あっても行を落とさない。一次観測を上書きしないという方針は、分析層でも変えない。
    どちらを採用するかはDerived側の規則で決める。
    """
    built_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    skipped = 0
    unmapped = 0
    for record in records:
        try:
            target_key = str(record["target_key"])
            obs_date = datetime.strptime(str(record["obs_date"]), "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        jpx_code = target_key_to_jpx_code(target_key)
        if jpx_code is None:
            unmapped += 1
        rows.append(
            {
                "target_key": target_key,
                "jpx_code": jpx_code,
                "source_key": "yfinance",
                "obs_date": obs_date,
                "open_price": record.get("open"),
                "high_price": record.get("high"),
                "low_price": record.get("low"),
                "close_price": record.get("close"),
                "volume": record.get("volume"),
                # yfinanceは auto_adjust=True で取得しているので調整済み。
                "price_basis": "adjusted",
                "ingestion_run_id": run_id,
                "built_at": built_at,
            }
        )
    return rows, skipped, unmapped


def build_price_rows(
    records: list[dict[str, Any]], run_id: int
) -> tuple[list[dict[str, Any]], int, int]:
    """1ファイルぶんのrawをParquet用の行へ変換する。

    戻り値は (行, 正規化できなかった件数, target_keyを引けなかった件数)。
    後者は行として残す。
    """
    built_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    skipped = 0
    unmapped = 0
    for record in records:
        try:
            price = normalize_jquants_price(record)
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        target_key = jpx_code_to_target_key(price.jpx_code)
        if target_key is None:
            unmapped += 1
        rows.append(
            {
                "target_key": target_key,
                "jpx_code": price.jpx_code,
                "source_key": JQUANTS_SOURCE_KEY,
                "obs_date": datetime.strptime(price.obs_date, "%Y-%m-%d").date(),
                "open_price": price.open_price,
                "high_price": price.high_price,
                "low_price": price.low_price,
                "close_price": price.close_price,
                "volume": price.volume,
                # J-Quantsの Adj* は調整済み。未調整と混ぜないために明示する。
                "price_basis": "adjusted",
                "ingestion_run_id": run_id,
                "built_at": built_at,
            }
        )
    return rows, skipped, unmapped


def deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """同じ (jpx_code, obs_date, source_key) を1行にする。最後の取込を残す。

    同じ日を複数回アーカイブすると、rawが日付ごとに独立しているぶんParquetでも重複する。
    実際に単日での動作確認とバックフィルが重なり、2日分が二重になった。値は同じでも
    行が二重だと、件数集計も時系列の窓関数も静かに狂う。

    残すのは後の取込。訂正があれば新しい方が正しい。
    """
    latest: dict[tuple[str, Any, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["jpx_code"], row["obs_date"], row["source_key"])
        current = latest.get(key)
        if current is None or (row["ingestion_run_id"] or 0) >= (
            current["ingestion_run_id"] or 0
        ):
            latest[key] = row
    return list(latest.values()), len(rows) - len(latest)


def write_year_partitions(rows: list[dict[str, Any]], out_dir: Path) -> dict[int, int]:
    """年で分けて書き出す。

    銘柄×月まで細かく割るとsmall filesが大量に出る。現在の規模（2年で約90MB）なら
    年単位で十分で、DuckDBはHive形式の `year=YYYY` をそのまま理解する。
    """
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[row["obs_date"].year].append(row)

    written: dict[int, int] = {}
    for year, year_rows in sorted(by_year.items()):
        target = out_dir / f"year={year}"
        target.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(year_rows, schema=PRICE_SCHEMA)
        pq.write_table(table, target / "part-0.parquet", compression="zstd")
        written[year] = len(year_rows)
    return written


def publish_parquet(local_dir: Path, destination: str) -> int:
    """出力したParquetをGCSへ同期する。

    **rawと違い上書きを許す。** rawは取得時点の事実なので一度書いたら変わらないが、
    Parquetは重複排除や変換の修正で作り直す派生物である。イミュータブルにすると
    作り直すたびに別パスへ増え、どれが最新か分からなくなる。

    `rsync --delete-unmatched-destination-objects` で、ローカルに無いオブジェクトを
    消す。年パーティションが減ったときに古い残骸が残らないようにする。
    """
    result = subprocess.run(
        [
            "gcloud", "storage", "rsync", "--recursive",
            "--delete-unmatched-destination-objects",
            str(local_dir), destination,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Parquetの公開に失敗しました: {result.stderr.strip()[:300]}")
    return result.returncode


# 取込種別ごとの変換。rawの形が取得元で違うため、1つの関数で分岐させず対応表で持つ。
PRICE_BUILDERS: dict[str, Callable[[list[dict[str, Any]], int], tuple[list[dict[str, Any]], int, int]]] = {
    "jquants_market_prices_archive_v1": build_price_rows,
    # 銘柄単位でJ-Quantsから取る経路（`JQUANTS_DAILY_ENABLED=true` のときの
    # `daily_update.py`、および `jquants_sync.py prices`）。rawの形は全市場アーカイブと
    # 同じで、同じエンドポイント・同じ正規化関数を通る。
    #
    # **ここに載せないとrawが分析層へ届かない。** 価格はParquetからしか読まないので、
    # 取得してPostgreSQLに入っているのに画面に出ない、という状態になる。
    "jquants_daily_prices_v1": build_price_rows,
    "yfinance_daily_prices_v1": build_yfinance_rows,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="rawレスポンスから分析用Parquetを組み立てる（rawが原本、Parquetは派生）"
    )
    parser.add_argument("--out", type=Path, default=Path("data/parquet"))
    parser.add_argument(
        "--job-type",
        action="append",
        dest="job_types",
        help="対象のrawを絞る取込種別。省略すると価格を持つ全取得元を読む",
    )
    parser.add_argument(
        "--publish",
        metavar="GS_URI",
        help="出力後にGCSへ同期する（例: gs://bucket/lake）",
    )
    parser.add_argument(
        "--base",
        metavar="LOCATION",
        help="土台にする既存Parquetの所在（gs://bucket/lake か ローカルパス）。"
             "指定するとそれより新しいrawだけを読む。日次はこれを使う",
    )
    args = parser.parse_args()
    if args.publish and not args.publish.startswith("gs://"):
        parser.error("--publish は gs:// で始まるURIを指定してください")

    project_root = Path(__file__).resolve().parents[2]
    job_types = args.job_types or list(PRICE_BUILDERS)
    all_rows: list[dict[str, Any]] = []
    skipped_total = 0
    unmapped_total = 0
    files = 0
    remote = 0
    per_source: dict[str, int] = {}

    watermark: int | None = None
    if args.base:
        all_rows, watermark = load_base_rows(
            f"{str(args.base).rstrip('/')}/observed/market_price", PRICE_SCHEMA.names
        )
        print(f"既存Parquet: {len(all_rows):,}行（取込実行 {watermark} まで）")

    connection = connect_database(read_only=True)
    try:
        for job_type in job_types:
            builder = PRICE_BUILDERS.get(job_type)
            if builder is None:
                raise SystemExit(
                    f"変換方法を知らない取込種別です: {job_type}"
                    f"（対応: {', '.join(PRICE_BUILDERS)}）"
                )
            before = len(all_rows)
            for location, records in iter_raw(
                connection, job_type,
                project_root=project_root, raw_dir=settings.raw_data_dir,
                after_run_id=watermark,
            ):
                rows, skipped, unmapped = builder(records, location.ingestion_run_id)
                all_rows.extend(rows)
                skipped_total += skipped
                unmapped_total += unmapped
                files += 1
                remote += 1 if location.is_remote else 0
            per_source[job_type] = len(all_rows) - before
    finally:
        connection.close()

    if not files and not all_rows:
        raise SystemExit(f"rawが見つかりません: {', '.join(job_types)}")
    if not files:
        # 差分更新で新しいrawが無いのは休場日などの正常系。既存を作り直さず終える。
        print("新しいrawはありません。Parquetは現状のままです")
        return 0

    all_rows, duplicates = deduplicate(all_rows)

    out_dir = args.out / "observed" / "market_price"
    written = write_year_partitions(all_rows, out_dir)

    print(f"raw {files}ファイル（GCS {remote} / ローカル {files - remote}） → 計 {len(all_rows):,}行")
    for job_type, count in per_source.items():
        print(f"  {job_type}: 追加 {count:,}行" if watermark else f"  {job_type}: {count:,}行")
    for year, count in written.items():
        print(f"  year={year}: {count:,}行")
    if duplicates:
        print(f"  重複を除外（同じ日を複数回アーカイブ）: {duplicates:,}行")
    if unmapped_total:
        print(f"  target_key未解決（行は保持、jpx_codeで識別可能）: {unmapped_total:,}行")
    if skipped_total:
        print(f"  正規化できず除外: {skipped_total:,}行")
    print(f"出力先: {out_dir}")

    if args.publish:
        destination = f"{args.publish.rstrip('/')}/observed/market_price"
        publish_parquet(out_dir, destination)
        print(f"公開先: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
