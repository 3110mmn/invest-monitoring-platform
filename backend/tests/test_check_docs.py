"""ドキュメント検査が実際に破綻を検出することを確認する。

検査そのものが素通りすると、緑のまま陳腐化が進む。検出できることを固定する。
"""

from scripts import check_docs
from scripts.check_docs import broken_links, forbidden_terms


def _write(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_broken_link_is_detected(tmp_path):
    """存在しないファイルへの参照を検出する。"""
    path = _write(tmp_path, "a.md", "詳細は [移動済み](./gone.md) を参照。")

    assert broken_links(path) == ["./gone.md"]


def test_existing_link_is_not_reported(tmp_path):
    """解決できる参照は報告しない。"""
    _write(tmp_path, "target.md", "# ある")
    path = _write(tmp_path, "a.md", "詳細は [ある](target.md) を参照。")

    assert broken_links(path) == []


def test_external_urls_and_anchors_are_skipped(tmp_path):
    """外部URLと同一文書内アンカーは検査しない。"""
    path = _write(
        tmp_path,
        "a.md",
        "[外部](https://example.test/x.md) [節](#section) [自分](a.md#節)",
    )

    assert broken_links(path) == []


def test_forbidden_term_is_detected(tmp_path):
    """実装と矛盾する語を行番号つきで検出する。"""
    path = _write(tmp_path, "a.md", "# 見出し\n\n接続時に PRAGMA foreign_keys を実行する。\n")

    problems = forbidden_terms(path)

    assert len(problems) == 1
    line, term, _reason = problems[0]
    assert (line, term) == (3, "PRAGMA")


def test_old_folder_path_is_detected(tmp_path):
    """再編前のフォルダパスを検出する。リンクとして解決できても表記は古い。"""
    path = _write(tmp_path, "a.md", "定義は `docs/database/schema-data-dictionary.md` にある。")

    assert [term for _line, term, _reason in forbidden_terms(path)] == ["docs/database/"]


def test_migration_context_is_allowed(tmp_path):
    """移行の経緯として述べている行は見逃す。"""
    path = _write(
        tmp_path,
        "a.md",
        "| `LEGACY_SQLITE_PATH` | 一度限りのSQLite移行元 |\n旧SQLiteの PRAGMA 設定は引き継がない。\n",
    )

    assert forbidden_terms(path) == []


def test_broken_backticked_path_is_detected(tmp_path, monkeypatch):
    """リンクではないパス記載も検査する。正本の表はこの形式を使う。"""
    monkeypatch.setattr(check_docs, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    path = _write(tmp_path, "a.md", "正本は `docs/gone/moved.md` にある。")

    assert [c for _line, c in check_docs.broken_backticked_paths(path)] == ["docs/gone/moved.md"]


def test_existing_backticked_path_is_not_reported(tmp_path, monkeypatch):
    """実在するパスは報告しない。"""
    monkeypatch.setattr(check_docs, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "here.md").write_text("x", encoding="utf-8")
    path = _write(tmp_path, "a.md", "正本は `docs/here.md` にある。")

    assert check_docs.broken_backticked_paths(path) == []


def test_non_repository_paths_are_ignored(tmp_path, monkeypatch):
    """GCSの接頭辞や記法まで拾わない。トップ階層が実在するものだけ検査する。"""
    monkeypatch.setattr(check_docs, "REPO_ROOT", tmp_path)
    path = _write(tmp_path, "a.md", "GCSの `raw/` と `postgres-backups/`、記法の `metric_id/value`。")

    assert check_docs.broken_backticked_paths(path) == []


def test_intentional_local_config_path_is_not_reported(tmp_path, monkeypatch):
    """Git管理外の正規なローカル設定パスはCIで存在しなくてもよい。"""
    monkeypatch.setattr(check_docs, "REPO_ROOT", tmp_path)
    (tmp_path / "backend").mkdir()
    path = _write(tmp_path, "a.md", "接続先は `backend/.env` で選ぶ。")

    assert check_docs.broken_backticked_paths(path) == []
