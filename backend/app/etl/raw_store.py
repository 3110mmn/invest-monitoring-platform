"""rawレスポンスの所在を `ingestion_run.raw_path` から解決して読み出す。

**rawの所在を知っているのはDBであり、filesystemではない。** `publish_pending_raw` が
GCSへ上げた後は `raw_path` が `gs://...` へ書き換わるため、ローカルを直接globすると
公開済みのrawを取りこぼす。逆にGCSだけを見ると、publish前のrawが読めない。

GitHub Actionsのランナーは使い捨てなので、ローカルに残るのは**その実行で取得した分だけ**
である。2年分のrawを横断する処理をCIで動かすには、DBの索引からGCSを読む経路が要る。

ここは読み出しのみを担当する。書き込みは `lineage.stage_raw`、GCSへの公開は
`raw_publisher.publish_pending_raw` が持つ。
"""

from __future__ import annotations

import gzip
import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.database import Connection

CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]

GCS_SCHEME = "gs://"


@dataclass(frozen=True)
class RawLocation:
    """1つのrawファイルの所在。"""

    ingestion_run_id: int
    raw_path: str

    @property
    def is_remote(self) -> bool:
        return self.raw_path.startswith(GCS_SCHEME)


def _resolve_local(raw_path: str, project_root: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def _drop_raw_lost_before_publication(
    locations: list[RawLocation], project_root: Path
) -> list[RawLocation]:
    """公開前に失われたrawを除外する。

    **`raw_path` が `gs://` かどうかが「永続化されたか」の印である。** 公開すると
    `raw_path` はGCSのURIへ書き換わる。ローカルパスのまま残っている取込は、rawが
    取得したマシンの上にしか存在しなかったということである。

    実際に2026-10-06の日次更新で、アーカイブは取得に成功したのに後続のステップが
    落ちてraw公開がスキップされ、5ファイルがランナーごと消えた。その状態で差分
    Parquetビルドを回すと、読めないファイルで `FileNotFoundError` になり、**1件の
    消失が以後のビルドを恒久的に止めた**。

    除外するのは「ローカルパスなのに実体が無い」ものだけに限る。`gs://` を指していて
    オブジェクトが無いのは保管の破損であり、黙って飛ばしてはいけない。そちらは
    `_read_remote` が `gcloud` の失敗として大きな音を立てる。
    """
    lost = [
        location
        for location in locations
        if not location.is_remote
        and not _resolve_local(location.raw_path, project_root).is_file()
    ]
    if not lost:
        return locations
    run_ids = ", ".join(str(location.ingestion_run_id) for location in lost)
    print(
        f"  ! 公開前に失われたrawを除外します: {len(lost)}ファイル（取込実行 {run_ids}）。"
        "取得は成功したが永続化されていないため、読める原本がありません。"
        "必要なら取り直して `python backend/scripts/publish_raw.py` で公開してください"
    )
    return [location for location in locations if location not in lost]


def find_raw_locations(
    connection: Connection,
    job_type: str,
    *,
    status: str = "succeeded",
    after_run_id: int | None = None,
    project_root: Path | None = None,
) -> list[RawLocation]:
    """指定した取込種別のrawの所在を、取込実行の順に返す。

    `raw_path` が NULL の実行は含めない。休場日など、取得はできたが中身が無い場合に
    NULL になる。これは失敗ではないので、読む対象が無いだけとして扱う。

    `after_run_id` を渡すと、その取込実行より後のものだけを返す。差分更新で使う。
    GitHub Actionsのランナーは使い捨てでローカルキャッシュが無く、2年分のrawを
    毎日GCSから読み直すと1ファイルずつの取得で30分を超えるため。

    `project_root` を渡すと、公開前に失われたrawを除外する。読み出し側は原本が
    1件失われただけで止まるべきではない。
    """
    query = """
        SELECT ingestion_run_id, raw_path
        FROM ingestion_run
        WHERE job_type = ? AND status = ? AND raw_path IS NOT NULL
    """
    params: tuple[Any, ...] = (job_type, status)
    if after_run_id is not None:
        query += " AND ingestion_run_id > ?"
        params += (after_run_id,)
    rows = connection.execute(query + " ORDER BY ingestion_run_id", params).fetchall()
    locations = [
        RawLocation(int(row["ingestion_run_id"]), str(row["raw_path"])) for row in rows
    ]
    if project_root is None:
        return locations
    return _drop_raw_lost_before_publication(locations, project_root)


def _cached_path(uri: str, raw_dir: Path | None) -> Path | None:
    """GCS URIに対応するローカルのキャッシュパスを返す。

    公開先は `<base>/<raw_dirからの相対パス>` の形なので、`raw/` 以降を切り出して
    `raw_dir` と組み合わせる。想定外の形なら None を返し、GCSから読ませる。
    """
    if raw_dir is None:
        return None
    marker = "/raw/"
    index = uri.find(marker)
    if index == -1:
        return None
    return raw_dir / uri[index + len(marker):]


def _read_local(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        data: list[dict[str, Any]] = json.load(stream)
        return data


def _read_remote(uri: str, runner: CommandRunner) -> list[dict[str, Any]]:
    """GCSのオブジェクトを標準出力へ取り出して読む。

    ローカルへ落とさないのは、2年分を順に読むときにディスクを埋めないため。
    """
    result = runner(
        ["gcloud", "storage", "cat", uri], capture_output=True, check=True
    )
    data: list[dict[str, Any]] = json.loads(gzip.decompress(result.stdout))
    return data


def read_raw(
    location: RawLocation,
    *,
    project_root: Path,
    raw_dir: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> list[dict[str, Any]]:
    """所在に応じてrawを読む。GCSとローカルのどちらでも同じ戻り値になる。

    GCSを指していても、同じ内容がローカルに残っていればそちらから読む。`publish` は
    ローカルを消さないため、公開後も手元には実体が残る。`gcloud` の起動コストは
    1ファイルあたり数秒で、500ファイルでは30分を超える。キャッシュを使えば数秒で済む。

    GCSが正でローカルはキャッシュという関係は変わらない。手元に無ければGCSから読む。
    """
    if location.is_remote:
        cached = _cached_path(location.raw_path, raw_dir)
        if cached is not None and cached.is_file():
            return _read_local(cached)
        return _read_remote(location.raw_path, runner)
    return _read_local(_resolve_local(location.raw_path, project_root))


def iter_raw(
    connection: Connection,
    job_type: str,
    *,
    project_root: Path,
    raw_dir: Path | None = None,
    runner: CommandRunner = subprocess.run,
    after_run_id: int | None = None,
) -> Iterator[tuple[RawLocation, list[dict[str, Any]]]]:
    """rawを1ファイルずつ読み出す。

    全件をメモリへ載せない。2年分の全市場価格は200万件を超えるため、
    呼び出し側が逐次処理できるようにする。

    `after_run_id` で読む範囲を絞れる。差分更新の入口。

    公開前に失われたrawは除外する。原本が1件失われただけでビルドが止まると、
    その後のすべての差分更新ができなくなる。
    """
    for location in find_raw_locations(
        connection, job_type, after_run_id=after_run_id, project_root=project_root
    ):
        yield location, read_raw(
            location, project_root=project_root, raw_dir=raw_dir, runner=runner
        )
