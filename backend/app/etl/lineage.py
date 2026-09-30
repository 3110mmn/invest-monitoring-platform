"""取込実行の記録と、rawレスポンスのローカル保存。

取得元ごとにこの処理を書くと、片方だけ来歴が欠ける。実際、J-Quants経路は
`ingestion_run`とrawを残していたが、yfinance経路は何も残しておらず、価格の観測は
どの実行で入ったのかもrawがどこにあるのかも追えない状態だった。

GCSへの公開は `raw_publisher.publish_pending_raw` が `raw_path` を見て行うため、
ここではローカルへ書くところまでを担当する。
"""

from __future__ import annotations

import gzip
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import Connection


def git_commit_sha(project_root: Path) -> str | None:
    """実行したコードのcommitを返す。取得できない環境ではNone。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root,
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def display_path(path: Path, project_root: Path) -> str:
    """DBにはプロジェクトルートからの相対パスで記録する。"""
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


_warned_about_unpublished_raw = False


def _warn_if_raw_has_nowhere_durable_to_go() -> None:
    """rawの公開先が未設定なら一度だけ警告する。

    **rawは原本である。** 手元にしか無い状態で放置すると、Parquetを作り直せる前提が
    静かに崩れる。実際に2年分の財務バックフィルを手元で流したとき、`RAW_DATA_URI` が
    未設定だったために487ファイルがこのマシンだけに残っていた。気づいたのは数日後で、
    その間ずっと原本が1か所にしかなかった。

    保存自体は止めない。取得できたものを捨てる方が損失が大きい。
    """
    global _warned_about_unpublished_raw
    if _warned_about_unpublished_raw:
        return
    from app.config import settings

    if settings.raw_data_uri:
        return
    _warned_about_unpublished_raw = True
    print(
        "  ! RAW_DATA_URI が未設定です。rawは手元にしか残りません。"
        "取得を続けるなら backend/.env に公開先を設定し、"
        "`python backend/scripts/publish_raw.py` で上げてください"
    )


def stage_raw(
    raw_dir: Path, source_key: str, job_type: str, run_id: int, records: list[dict[str, Any]]
) -> Path:
    """rawレスポンスをローカルへgzipで保存し、そのパスを返す。

    取得元をパスへ含めるのは、GCSへ上げたあとに取得元をまたいで探せるようにするため。

    公開先が未設定なら警告する。rawは原本なので、手元にしか無い状態に気づかせる。
    """
    _warn_if_raw_has_nowhere_durable_to_go()
    now = datetime.now(UTC)
    target = raw_dir / source_key / job_type / now.strftime("%Y/%m/%d") / f"run-{run_id}.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt", encoding="utf-8") as stream:
        json.dump(records, stream, ensure_ascii=False, separators=(",", ":"))
    return target


def start_run(
    conn: Connection,
    source_id: int,
    job_type: str,
    project_root: Path,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    target_count: int = 0,
) -> int:
    row = conn.execute(
        """
        INSERT INTO ingestion_run (
            job_type, git_commit_sha, source_id, status, requested_from, requested_to, target_count
        ) VALUES (?, ?, ?, 'running', ?, ?, ?)
        RETURNING ingestion_run_id
        """,
        (job_type, git_commit_sha(project_root), source_id, date_from, date_to, target_count),
    ).fetchone()
    if row is None:
        raise RuntimeError("ingestion_run の作成に失敗しました")
    return int(row["ingestion_run_id"])


def finish_run(
    conn: Connection,
    run_id: int,
    *,
    status: str,
    fetched: int,
    loaded: int,
    skipped: int,
    failed: int,
    raw_path: str | None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_run
        SET status = ?, fetched_count = ?, loaded_count = ?, skipped_count = ?,
            failed_count = ?, raw_path = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP
        WHERE ingestion_run_id = ?
        """,
        (status, fetched, loaded, skipped, failed, raw_path, error_message, run_id),
    )
