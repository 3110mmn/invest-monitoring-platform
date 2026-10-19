"""ドキュメントの機械的な破綻を検出する。

このプロジェクトはドキュメントが多く、手作業の確認では次の2種類を取りこぼす。

1. **リンク切れ** — ファイルを移動・削除したときに残る参照
2. **実装と矛盾する語** — 移行や設計変更のあとに残る古い用語

どちらも「読めば分かる」が「読まないと分からない」ため、CIで落とす。
スキーマとデータ定義書の整合は `generate_schema_docs.py --check` が担当する。

使い方:
    python backend/scripts/check_docs.py
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 検査対象。docs配下に加えて、入口となるファイルを含める。
# `research/` と `orchestration/` のREADMEも公開スナップショットへ出るため含める。
# 検査の外にあると、リンク切れや古いパスが気づかれないまま公開される。
TARGET_GLOBS = (
    "docs/**/*.md",
    "README.md",
    ".claude/CLAUDE.md",
    "research/README.md",
    "orchestration/**/*.md",
)

# 実装と矛盾する語。移行や設計変更で意味が変わったものを、理由つきで登録する。
# 経緯として言及してよい箇所は ALLOWED_CONTEXT で除外する。
FORBIDDEN_TERMS: dict[str, str] = {
    "PRAGMA": "SQLite固有。PostgreSQLへ移行済み",
    "AUTOINCREMENT": "SQLite固有。`GENERATED ALWAYS AS IDENTITY` を使う",
    "canonical_db.py": "GCS同期の暫定スクリプト。PostgreSQL移行で廃止済み",
    "snapshot_db.py": "SQLiteスナップショット生成。PostgreSQL移行で廃止済み",
    "db_bootstrap.py": "起動時のGCS取得。PostgreSQL移行で廃止済み",
    "docs/diagrams/": "旧フォルダ構成。architecture / data へ再編済み",
    "docs/database/": "旧フォルダ構成。data へ再編済み",
    "docs/references/": "旧フォルダ構成。data/sources へ再編済み",
    "docs/traceability/": "旧フォルダ構成。data/sources/<取得元>/ へ再編済み",
    "docs/operations/": "旧フォルダ構成。infrastructure へ再編済み",
}

# 移行の経緯として言及してよい文脈。行内にこれを含むなら見逃す。
ALLOWED_CONTEXT = (
    "GCS SQLite",
)

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# バッククォートで囲まれた、リポジトリ内のパスらしき文字列。
# `.claude/CLAUDE.md` の「正本はどこにあるか」の表はリンクではなくこの形式のため、
# docs再編でパスが動いてもリンク検査では捕まらず、7件が壊れたまま残っていた。
BACKTICKED_PATH = re.compile(r"`((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*)`")

# プレースホルダ記法は実在しなくてよい。
PATH_CHECK_EXCEPTIONS = ("<", "http")

# Git管理外だが、利用者がローカルに作成する正規の設定パス。
# CIで実体が無いのは意図どおりなので、移動・削除漏れとして扱わない。
INTENTIONAL_LOCAL_PATHS = {"backend/.env", "backend/.env.neon"}


def is_intentionally_ignored(candidate: str) -> bool:
    """`.gitignore` が意図的に除外しているパスかを返す。

    **分析結果の出力先のように、実体が無いのが正しい記述がある。** 手元には存在
    するためローカルでは気づけず、CIだけが落ちる。個別に列挙すると増えるたびに
    追記が要るので、除外設定そのものへ問い合わせる。
    """
    if candidate in INTENTIONAL_LOCAL_PATHS:
        return True
    result = subprocess.run(
        ["git", "check-ignore", "-q", candidate],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.returncode == 0


def target_files() -> list[Path]:
    """検査対象のMarkdownを返す。"""
    files: list[Path] = []
    for pattern in TARGET_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return files


def broken_links(path: Path) -> list[str]:
    """解決できないリンク先を返す。外部URLとアンカーのみの参照は対象外。"""
    problems = []
    for target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # 同じファイル内のアンカーを落として実体だけ見る
        relative = target.split("#", 1)[0]
        if not relative:
            continue
        if not (path.parent / relative).exists():
            problems.append(relative)
    return problems


def broken_backticked_paths(path: Path) -> list[tuple[int, str]]:
    """バッククォート内のリポジトリパスのうち、実在しないものを返す。

    リンク形式でない参照はリンク検査を素通りする。正本の所在を表で示す文書は
    この形式を使うため、ここを見ないと移動に気づけない。
    """
    # リポジトリのトップ階層で始まるものだけを対象にする。そうしないとGCSの
    # バケット接頭辞（`raw/`）や `metric_id/value` のような記法まで拾ってしまう。
    top_level = {entry.name for entry in REPO_ROOT.iterdir()}

    problems = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for candidate in BACKTICKED_PATH.findall(line):
            if any(token in candidate for token in PATH_CHECK_EXCEPTIONS):
                continue
            if is_intentionally_ignored(candidate):
                continue
            if (REPO_ROOT / candidate).exists():
                continue
            if candidate.split("/", 1)[0] in top_level:
                problems.append((number, candidate))
                continue
            # **トップ階層から書かれていない参照も拾う。** `app/repositories/...` のように
            # 途中から書くと、リポジトリ直下に無いためこれまで素通りしていた。実在する
            # ファイルの末尾と一致するなら、省略形として扱い正しいパスを促す。
            #
            # 拡張子を持つものだけを見る。`observed/financial_summary` のような
            # GCS prefix はリポジトリのパスではなく、偶然ディレクトリ名と一致する。
            if not Path(candidate).suffix:
                continue
            # 文書からの相対パスとして解決できるなら正しい記述。
            if (path.parent / candidate).exists():
                continue
            matches = [
                found.relative_to(REPO_ROOT).as_posix()
                for found in REPO_ROOT.rglob(Path(candidate).name)
                if found.relative_to(REPO_ROOT).as_posix().endswith(candidate)
                and ".git/" not in found.as_posix()
            ]
            if matches:
                problems.append((number, f"{candidate}（{matches[0]} では？）"))
    return problems


def forbidden_terms(path: Path) -> list[tuple[int, str, str]]:
    """実装と矛盾する語を、行番号つきで返す。"""
    problems = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if any(allowed in line for allowed in ALLOWED_CONTEXT):
            continue
        for term, reason in FORBIDDEN_TERMS.items():
            if term in line:
                problems.append((number, term, reason))
    return problems


# 実測して確かめられる数値。書いた時点では正しくても、増減すると黙って嘘になる。
# 「何件か」を人が数え直すのは続かないので、収集方法とセットで持つ。
# 「migration NNNNまで適用する」のように、現在のheadを指す記述は追加のたびに腐る。
# 特定のmigrationを経緯として参照するのは正しいので、「まで」を伴う場合だけ見る。
MIGRATION_HEAD_CLAIM = re.compile(r"migration\s+(\d{4})[_\w]*\s*まで")


def stale_migration_head(path: Path) -> list[tuple[int, str, str]]:
    """現在のheadと食い違うmigration参照を返す。"""
    versions = sorted(
        entry.name[:4]
        for entry in (REPO_ROOT / "backend/migrations/versions").glob("[0-9]*.py")
    )
    if not versions:
        return []
    head = versions[-1]
    problems: list[tuple[int, str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = MIGRATION_HEAD_CLAIM.search(line)
        if match and match.group(1) != head:
            problems.append((number, match.group(0), head))
    return problems


# テストの件数は書かない。読み手には「348件」が多いのか少ないのか、
# そのテストが何を守っているのかが分からず、情報量がほぼ無い。一方でコミット
# するたびに腐り、同じREADME内で348件と293件が同居していたこともある。
# 伝える価値があるのは件数ではなく「実PostgreSQLに対して実行する」という性質。
TEST_COUNT_CLAIM = re.compile(r"(?:バックエンド|フロントエンド|backend|frontend)?\s*"
                              r"テスト\s*[0-9,]+\s*件")


def test_count_claims(path: Path) -> list[tuple[int, str]]:
    """テスト件数の記述を返す。書いた時点で腐り始めるため置かない。"""
    problems: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = TEST_COUNT_CLAIM.search(line)
        if match:
            problems.append((number, match.group(0)))
    return problems


def main() -> int:
    link_problems: list[str] = []
    path_problems: list[str] = []
    term_problems: list[str] = []
    count_problems: list[str] = []

    for path in target_files():
        shown = path.relative_to(REPO_ROOT)
        for target in broken_links(path):
            link_problems.append(f"  {shown} → {target}")
        for number, candidate in broken_backticked_paths(path):
            path_problems.append(f"  {shown}:{number} `{candidate}`")
        for number, term, reason in forbidden_terms(path):
            term_problems.append(f"  {shown}:{number} 「{term}」— {reason}")
        for number, written in test_count_claims(path):
            count_problems.append(
                f"  {shown}:{number} 「{written}」— 件数は書かない（腐る。性質を書く）"
            )
        for number, written, head in stale_migration_head(path):
            count_problems.append(f"  {shown}:{number} 「{written}」— 現在のheadは{head}")

    if link_problems:
        print("リンク切れ:")
        print("\n".join(link_problems))
    if path_problems:
        print("実在しないパスの記載:")
        print("\n".join(path_problems))
        print("\n  移動・削除されたファイルを指しています。現行のパスへ直してください")
    if term_problems:
        print("実装と矛盾する語:")
        print("\n".join(term_problems))
        print("\n  経緯として言及する必要がある場合は check_docs.py の ALLOWED_CONTEXT へ追加する")

    if count_problems:
        print("腐る数値の記述:")
        print("\n".join(count_problems))
        print("\n  実測して確かめられる数値は、増減すると黙って嘘になる。性質を書く")

    if link_problems or path_problems or term_problems or count_problems:
        return 1
    print(f"ドキュメント {len(target_files())} 件を検査。問題ありません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
