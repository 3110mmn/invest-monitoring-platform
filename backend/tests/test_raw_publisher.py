import subprocess
from pathlib import Path

import pytest

from app.database import Connection
from app.etl.raw_publisher import publish_pending_raw


def _ingestion_run(db: Connection, raw_path: str) -> int:
    source_id = db.execute(
        """
        INSERT INTO data_source (source_key, source_name)
        VALUES ('raw-test', 'Raw Test')
        RETURNING source_id
        """
    ).fetchone()["source_id"]
    return int(
        db.execute(
            """
            INSERT INTO ingestion_run (job_type, source_id, status, raw_path)
            VALUES ('raw_test_v1', ?, 'succeeded', ?)
            RETURNING ingestion_run_id
            """,
            (source_id, raw_path),
        ).fetchone()["ingestion_run_id"]
    )


def _ingestion_runs(db: Connection, raw_paths: list[str]) -> None:
    """同じ取得元で複数の取込実行を作る。`_ingestion_run`は取得元ごと作るため使えない。"""
    source_id = db.execute(
        """
        INSERT INTO data_source (source_key, source_name)
        VALUES ('raw-test-multi', 'Raw Test Multi')
        RETURNING source_id
        """
    ).fetchone()["source_id"]
    for raw_path in raw_paths:
        db.execute(
            """
            INSERT INTO ingestion_run (job_type, source_id, status, raw_path)
            VALUES ('raw_test_v1', ?, 'succeeded', ?)
            """,
            (source_id, raw_path),
        )


def test_publish_pending_raw_uploads_then_rewrites_path(
    db: Connection, tmp_path: Path
) -> None:
    raw_dir = tmp_path / "data" / "raw"
    raw_file = raw_dir / "jquants" / "job" / "run-1.json.gz"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"raw-content")
    raw_path = str(raw_file.relative_to(tmp_path))
    run_id = _ingestion_run(db, raw_path)
    commands: list[list[str]] = []

    def successful_runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = publish_pending_raw(
        db,
        raw_dir=raw_dir,
        project_root=tmp_path,
        destination_base="gs://bucket/raw",
        runner=successful_runner,
    )

    row = db.execute(
        "SELECT raw_path FROM ingestion_run WHERE ingestion_run_id = ?", (run_id,)
    ).fetchone()
    assert result == {"uploaded": 1, "missing": 0}
    assert row["raw_path"] == "gs://bucket/raw/jquants/job/run-1.json.gz"
    assert commands[0][:3] == ["gcloud", "storage", "cp"]
    assert "--if-generation-match=0" in commands[0]
    assert any(part.startswith("--custom-metadata=sha256=") for part in commands[0])


def test_publish_pending_raw_rejects_path_outside_raw_dir(
    db: Connection, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.json.gz"
    outside.write_bytes(b"raw")
    _ingestion_run(db, str(outside))

    with pytest.raises(RuntimeError, match="escapes configured raw directory"):
        publish_pending_raw(
            db,
            raw_dir=tmp_path / "data" / "raw",
            project_root=tmp_path,
            destination_base="gs://bucket/raw",
        )


def test_uploads_run_in_parallel(db: Connection, tmp_path: Path) -> None:
    """アップロードを並列に行う。

    `gcloud`の起動に1ファイルあたり約3秒かかるため、逐次では20年分のバックフィル
    （約4,900ファイル）が4時間を超える。同時に走っていることを、重なりの観測で確かめる。
    """
    import threading
    import time

    raw_dir = tmp_path / "raw"
    paths = []
    for index in range(6):
        path = raw_dir / f"run-{index}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        paths.append(f"raw/run-{index}.json.gz")
    _ingestion_runs(db, paths)
    db.commit()

    lock = threading.Lock()
    active = 0
    peak = 0

    def runner(args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result = publish_pending_raw(
        db,
        raw_dir=raw_dir,
        project_root=tmp_path,
        destination_base="gs://bucket/raw",
        runner=runner,
        max_workers=4,
    )

    assert result["uploaded"] == 6
    assert peak > 1, "逐次実行になっている"


def test_a_failed_upload_does_not_mark_others_published(
    db: Connection, tmp_path: Path
) -> None:
    """1件でも失敗したら中断し、raw_pathを書き換えない。

    公開できていないものを公開済みとして記録すると、次回以降の対象から外れて
    取りこぼす。並列化しても、この性質は変えない。
    """
    raw_dir = tmp_path / "raw"
    paths = []
    for index in range(4):
        path = raw_dir / f"run-{index}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        paths.append(f"raw/run-{index}.json.gz")
    _ingestion_runs(db, paths)
    db.commit()

    def runner(args, **kwargs):
        if "run-2.json.gz" in " ".join(str(a) for a in args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with pytest.raises(RuntimeError):
        publish_pending_raw(
            db,
            raw_dir=raw_dir,
            project_root=tmp_path,
            destination_base="gs://bucket/raw",
            runner=runner,
            max_workers=4,
        )

    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM ingestion_run WHERE raw_path NOT LIKE ?",
        ("gs://%",),
    ).fetchone()["n"]
    assert remaining == 4, "失敗したのに公開済みとして記録された行がある"
