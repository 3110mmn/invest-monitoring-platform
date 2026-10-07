"""rawの所在解決を確認する。

rawの所在を知っているのは ingestion_run.raw_path であり filesystem ではない。
publish後は gs:// を指すため、ローカルを直接globすると公開済みのrawを取りこぼす。
"""

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from app.etl.raw_store import RawLocation, find_raw_locations, iter_raw, read_raw

RECORDS = [{"Code": "72030", "Date": "2026-07-01"}]


def _stage(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(RECORDS, stream)
    return path


def _fake_runner(payload: bytes):
    def runner(args, **kwargs):
        assert args[:3] == ["gcloud", "storage", "cat"]
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr=b"")

    return runner


def _insert_run(db, source_id: int, job_type: str, status: str, raw_path: str | None) -> int:
    row = db.execute(
        """
        INSERT INTO ingestion_run (job_type, source_id, status, raw_path)
        VALUES (?, ?, ?, ?) RETURNING ingestion_run_id
        """,
        (job_type, source_id, status, raw_path),
    ).fetchone()
    return int(row["ingestion_run_id"])


@pytest.fixture
def source_id(db) -> int:
    row = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES ('jquants', 'J-Quants') "
        "RETURNING source_id"
    ).fetchone()
    return int(row["source_id"])


def test_local_and_remote_reads_return_the_same_records(tmp_path):
    """所在が違っても戻り値は同じ。呼び出し側が置き場所を意識しないようにする。"""
    local = read_raw(
        RawLocation(1, str(_stage(tmp_path, "run-1.json.gz"))), project_root=tmp_path
    )
    payload = gzip.compress(json.dumps(RECORDS).encode("utf-8"))
    remote = read_raw(
        RawLocation(2, "gs://bucket/raw/run-2.json.gz"),
        project_root=tmp_path,
        runner=_fake_runner(payload),
    )

    assert local == remote == RECORDS


def test_remote_paths_are_detected():
    assert RawLocation(1, "gs://bucket/x.json.gz").is_remote is True
    assert RawLocation(2, "data/raw/x.json.gz").is_remote is False


def test_published_raw_is_not_missed(db, source_id):
    """GCSへ公開済みのrawも索引に含める。ローカルだけ見ると取りこぼす。"""
    _insert_run(db, source_id, "job", "succeeded", "data/raw/run-1.json.gz")
    _insert_run(db, source_id, "job", "succeeded", "gs://bucket/raw/run-2.json.gz")

    locations = find_raw_locations(db, "job")

    assert [loc.is_remote for loc in locations] == [False, True]


def test_runs_without_raw_are_skipped(db, source_id):
    """休場日などraw_pathがNULLの実行は読む対象が無いだけで、失敗ではない。"""
    _insert_run(db, source_id, "job", "succeeded", None)

    assert find_raw_locations(db, "job") == []


def test_failed_runs_are_excluded(db, source_id):
    """失敗した実行のrawは不完全なので分析へ入れない。"""
    _insert_run(db, source_id, "job", "failed", "data/raw/run-1.json.gz")

    assert find_raw_locations(db, "job") == []


def test_other_job_types_are_excluded(db, source_id):
    _insert_run(db, source_id, "prices", "succeeded", "data/raw/run-1.json.gz")
    _insert_run(db, source_id, "financials", "succeeded", "data/raw/run-2.json.gz")

    assert [loc.raw_path for loc in find_raw_locations(db, "prices")] == [
        "data/raw/run-1.json.gz"
    ]


def test_iter_raw_yields_one_file_at_a_time(db, source_id, tmp_path):
    """全件をメモリへ載せない。2年分の全市場価格は200万件を超える。"""
    _stage(tmp_path, "run-1.json.gz")
    _insert_run(db, source_id, "job", "succeeded", "run-1.json.gz")

    results = list(iter_raw(db, "job", project_root=tmp_path))

    assert len(results) == 1
    location, records = results[0]
    assert location.is_remote is False
    assert records == RECORDS


def test_local_cache_is_used_when_available(tmp_path):
    """GCSを指していても、手元に実体があればそちらを読む。

    publish はローカルを消さない。gcloud の起動コストは1ファイル数秒で、500ファイルでは
    30分を超える。GCSが正でローカルはキャッシュ、という関係は変えない。
    """
    raw_dir = tmp_path / "raw"
    (raw_dir / "jquants" / "job" / "2026" / "07" / "01").mkdir(parents=True)
    cached = raw_dir / "jquants" / "job" / "2026" / "07" / "01" / "run-1.json.gz"
    with gzip.open(cached, "wt", encoding="utf-8") as stream:
        json.dump(RECORDS, stream)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("ローカルにあるのにGCSを読んでいる")

    records = read_raw(
        RawLocation(1, "gs://bucket/raw/jquants/job/2026/07/01/run-1.json.gz"),
        project_root=tmp_path,
        raw_dir=raw_dir,
        runner=_must_not_run,
    )

    assert records == RECORDS


def test_falls_back_to_gcs_when_cache_is_absent(tmp_path):
    """手元に無ければGCSから読む。CIのランナーにはキャッシュが無い。"""
    payload = gzip.compress(json.dumps(RECORDS).encode("utf-8"))

    records = read_raw(
        RawLocation(1, "gs://bucket/raw/jquants/job/2026/07/01/run-1.json.gz"),
        project_root=tmp_path,
        raw_dir=tmp_path / "empty",
        runner=_fake_runner(payload),
    )

    assert records == RECORDS


def test_cache_lookup_ignores_unexpected_uri_shapes(tmp_path):
    """想定外のURI形ならキャッシュを探さずGCSへ行く。誤ったファイルを読まない。"""
    from app.etl.raw_store import _cached_path

    assert _cached_path("gs://bucket/other/x.json.gz", tmp_path) is None
    assert _cached_path("gs://bucket/raw/a/b.json.gz", None) is None


def test_after_run_id_reads_only_newer_raw(db, source_id):
    """差分更新は基準線より後のrawだけを読む。

    CIのランナーは使い捨てでキャッシュが無く、2年分977ファイルを毎日GCSから
    読み直すと30分を超える。既にParquetにある分は読み直さない。
    """
    first = _insert_run(db, source_id, "job", "succeeded", "data/raw/run-1.json.gz")
    _insert_run(db, source_id, "job", "succeeded", "data/raw/run-2.json.gz")

    locations = find_raw_locations(db, "job", after_run_id=first)

    assert [loc.raw_path for loc in locations] == ["data/raw/run-2.json.gz"]


def test_after_run_id_at_the_latest_run_reads_nothing(db, source_id):
    """新しいrawが無ければ空。休場日の正常系で、失敗として扱わない。"""
    _insert_run(db, source_id, "job", "succeeded", "data/raw/run-1.json.gz")
    latest = _insert_run(db, source_id, "job", "succeeded", "data/raw/run-2.json.gz")

    assert find_raw_locations(db, "job", after_run_id=latest) == []


def test_without_after_run_id_everything_is_read(db, source_id):
    """基準線を渡さなければ全件。全再構築の経路は変わらない。"""
    _insert_run(db, source_id, "job", "succeeded", "data/raw/run-1.json.gz")
    _insert_run(db, source_id, "job", "succeeded", "data/raw/run-2.json.gz")

    assert len(find_raw_locations(db, "job")) == 2


def test_raw_lost_before_publication_does_not_block_reads(db, source_id, tmp_path, capsys):
    """公開前に失われたrawは除外する。1件の消失が以後のビルドを止めないようにする。

    2026-10-06の日次更新で、アーカイブは取得に成功したのに後続ステップが落ちてraw公開が
    スキップされ、5ファイルがランナーごと消えた。その状態で差分Parquetビルドを回すと
    読めないファイルで落ち、消失が解消されない限り永久にビルドできなくなっていた。
    """
    _stage(tmp_path, "run-2.json.gz")
    _insert_run(db, source_id, "job", "succeeded", "run-1.json.gz")  # 実体が無い
    _insert_run(db, source_id, "job", "succeeded", "run-2.json.gz")

    results = list(iter_raw(db, "job", project_root=tmp_path))

    assert [location.raw_path for location, _ in results] == ["run-2.json.gz"]
    assert "公開前に失われたraw" in capsys.readouterr().out, (
        "黙って飛ばすと、原本が失われたことに気づけない"
    )


def test_published_raw_is_kept_even_when_absent_locally(db, source_id, tmp_path):
    """`gs://` を指すrawは手元に無くても除外しない。

    GCSが正でローカルはキャッシュである。公開済みのオブジェクトが読めないのは保管の
    破損であり、黙って飛ばしてはいけない。読み出しの失敗として大きな音を立てる。
    """
    _insert_run(db, source_id, "job", "succeeded", "gs://bucket/raw/run-1.json.gz")

    locations = find_raw_locations(db, "job", project_root=tmp_path)

    assert [loc.raw_path for loc in locations] == ["gs://bucket/raw/run-1.json.gz"]


def test_lost_raw_is_only_dropped_when_asked(db, source_id, tmp_path):
    """`project_root` を渡さない呼び出しの振る舞いは変えない。

    所在の索引を引くだけの用途では、filesystemを見に行かない。
    """
    _insert_run(db, source_id, "job", "succeeded", "run-1.json.gz")

    assert len(find_raw_locations(db, "job")) == 1
