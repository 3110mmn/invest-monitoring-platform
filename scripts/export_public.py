"""公開用リポジトリへ出すスナップショットを書き出す。

このリポジトリの履歴には実データ（`data/invest.db`）を含むコミットが225件あり、
`.gitignore` へ追加しても過去からは取り出せる。したがって公開は履歴ごとではなく、
**現在のHEADのツリーだけ**を新しいリポジトリへ出す形で行う。

このスクリプトは出力先ディレクトリを作るところまでを担当する。commitとpushは行わない。
公開は取り消せないため、中身を確認してから人が実行する。

使い方:
    python scripts/export_public.py --out /tmp/invest-monitoring-platform
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 公開ツリーから除外するもの。追跡されていても出さない。
EXCLUDE_PREFIXES = (
    # 開発者向けのAI作業設定であり、成果物ではない
    ".claude/",
    # Git管理外だが、取り違え防止のため明示する
    "memo/",
    "data/",
    # 探索分析。何を検討して何を捨てたか、どの銘柄を見ているかが読み取れる。
    # 利用規約には触れないが、個人の関心と進行中の検討を公開する必要は無い。
    # 継続運用へ昇格した計算定義は `backend/analytics/derived/` と `analytics/dbt/`
    # にあり、そちらは公開する。
    "research/",
)

EXCLUDE_EXACT = (
    # 開発者向けのAI作業設定。共通モデルへのローカル相対パスを含むため公開しない。
    "AGENTS.md",
    "CLAUDE.md",
    # 日次ETLは実行ログに銘柄名と終値を出す。公開リポジトリのActionsログは
    # 誰でも読めるため、監視銘柄が公開され続ける。運用は非公開側に残す。
    # 公開側に置くとSecretが無く、スケジュール実行が毎日失敗して赤くなる問題もある。
    ".github/workflows/daily-update.yml",
    # 実運用DBのcleanup用。公開スナップショットのActionsに含めない。
    ".github/workflows/cleanup-public-demo.yml",
    # `research/`を除外するため、それをimportするテストも出せない。公開側のCIは
    # `backend/tests`を丸ごと収集するので、残すと`ModuleNotFoundError`で必ず落ちる。
    # 検査対象の実体（`research/pbr_value_trap.py`）と同じ単位で外す。
    "backend/tests/test_pbr_research.py",
    # dbt docsの生成はBigQueryへの認証を要し、`GCP_CREDENTIALS`は非公開側にしか無い。
    # 公開側へ置くとpushのたびに失敗して赤くなる。生成は非公開側で行い、成果物だけを
    # `--dbt-docs`で公開ツリーへ取り込む。
    ".github/workflows/publish-dbt-docs.yml",
)

# dbt docsの静的サイトを置く公開ツリー上の位置。GitHub PagesのSource設定で
# `/docs`を選べるため、公開リポジトリ側の設定だけで配信できる。
DBT_DOCS_DESTINATION = "docs/dbt"

# 配信に要るファイル。`index.html`が読むのはmanifestとcatalogだけで、compiled SQLや
# 実行ログは持ち込まない。
DBT_DOCS_FILES = ("index.html", "manifest.json", "catalog.json")

# 出力ツリーに1つでも見つかったら公開を中止する。除外リストの漏れを最後に捕まえる。
SECRET_PATTERNS = {
    r"npg_[A-Za-z0-9]{8,}": "Neonのパスワード",
    r"neondb_owner": "NeonのOwnerロール名",
    r"ep-[a-z]+-[a-z]+-[a-z0-9]+\.[a-z0-9-]+\.aws\.neon\.tech": "Neonの実エンドポイント",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----": "秘密鍵",
    r"ghp_[A-Za-z0-9]{20,}": "GitHubトークン",
    r"github_pat_[A-Za-z0-9_]{20,}": "GitHubトークン",
    r"AKIA[0-9A-Z]{16}": "AWSアクセスキー",
    r'"type"\s*:\s*"service_account"': "GCPサービスアカウント鍵",
    r"X-Admin-Key\s*[:=]\s*['\"][^'\"]{8,}": "管理APIキー",
}

# 走査しないファイル。バイナリは誤検知と遅延を避けるため。
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".db"}

# このファイル自身は検出パターンの定義を含むため、走査すると必ず自分に当たる。
# 唯一の除外であり、中身は上のリテラルだけだと分かっているので安全に外せる。
SCAN_SKIP = {"scripts/export_public.py"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def is_excluded(path: str) -> bool:
    return path in EXCLUDE_EXACT or path.startswith(EXCLUDE_PREFIXES)


def scan_for_secrets(root: Path) -> list[str]:
    """出力ツリー全体を走査し、見つかった秘密を報告する。"""
    findings: list[str] = []
    compiled = {re.compile(p): label for p, label in SECRET_PATTERNS.items()}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        if path.relative_to(root).as_posix() in SCAN_SKIP:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, label in compiled.items():
                if pattern.search(line):
                    findings.append(f"  {path.relative_to(root)}:{number} {label}")
    return findings


MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def dangling_references(root: Path, excluded: list[str]) -> list[str]:
    """公開ツリーの文書が、そこに存在しないものを案内していないか調べる。

    除外は公開ツリーの中に「存在しないものへの参照」を作る。公開してから気づくと
    直しようがないので、ここで止める。

    **ファイル名ではなくパスで判定する。** 以前は除外ファイルのbasenameを部分一致で
    探していたため、`memo/plan/README.md` を除外するとルートの `README.md` が、
    `memo/jquants/data-dictionary.md` を除外すると `data-definitions/postgresql.md` が
    誤検出された。リンクは書かれた位置から解決し、公開ツリーに実体があるかを見る。
    """
    excluded_paths = set(excluded)
    problems: list[str] = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if relative.as_posix() in SCAN_SKIP:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for target in MARKDOWN_LINK.findall(line):
                target = target.split("#", 1)[0].split(" ", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (path.parent / target).resolve()
                try:
                    resolved.relative_to(root.resolve())
                except ValueError:
                    problems.append(f"  {relative}:{number} 公開ツリーの外を指す「{target}」")
                    continue
                if not resolved.exists():
                    problems.append(f"  {relative}:{number} リンク切れ「{target}」")
            # リンク以外の言及も拾う。除外したパスをそのまま書いている場合。
            for excluded_path in excluded_paths:
                if excluded_path in line:
                    problems.append(f"  {relative}:{number} 除外した「{excluded_path}」")
    return problems


# 除外したトップレベルパッケージをimportしている公開対象のPythonを探す。
# markdownのリンク切れは`dangling_references`が見るが、importは見ていなかった。
# `research/`を外したとき`backend/tests/test_pbr_research.py`が公開側に残り、
# CIが`ModuleNotFoundError`で落ちた。公開してから気づいたので検査へ足す。
IMPORT_STATEMENT = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][A-Za-z0-9_]*)[.\s]|import\s+([A-Za-z_][A-Za-z0-9_]*))",
    re.MULTILINE,
)


def dangling_imports(root: Path, excluded: list[str]) -> list[str]:
    """公開ツリーのPythonが、除外したパッケージをimportしていないか調べる。"""
    packages = {
        path.split("/", 1)[0]
        for path in excluded
        if "/" in path and (REPO_ROOT / path.split("/", 1)[0] / "__init__.py").exists()
    }
    if not packages:
        return []
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in SCAN_SKIP:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            match = IMPORT_STATEMENT.match(line)
            if not match:
                continue
            name = match.group(1) or match.group(2)
            if name in packages:
                problems.append(
                    f"  {relative}:{number} 除外したパッケージ「{name}」をimportしている"
                )
    return problems


def export(destination: Path) -> tuple[list[str], list[str]]:
    included, excluded = [], []
    for relative in tracked_files():
        if is_excluded(relative):
            excluded.append(relative)
            continue
        source = REPO_ROOT / relative
        if not source.exists():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        included.append(relative)
    return included, excluded


def copy_dbt_docs(source: Path, destination: Path) -> list[str]:
    """生成済みのdbt docsを公開ツリーへ取り込む。

    **生成はしない。** `dbt docs generate`はBigQueryへの認証を要するため、非公開側の
    workflowで作ったartifactを展開したディレクトリを受け取る。

    取り込む前に`check_dbt_docs_safe.py`と同じ検査を通す。workflowでも検査しているが、
    artifactを手で差し替えられる以上、公開の直前にもう一度確かめる。
    """
    missing = [name for name in DBT_DOCS_FILES if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            f"中止しました。dbt docsに必要なファイルがありません: {', '.join(missing)}"
        )

    check = subprocess.run(
        ["python", str(REPO_ROOT / "scripts/check_dbt_docs_safe.py"), str(source)],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise SystemExit(
            "中止しました。dbt docsに実データが含まれています。\n"
            f"{check.stdout}{check.stderr}"
        )

    target_dir = destination / DBT_DOCS_DESTINATION
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in DBT_DOCS_FILES:
        shutil.copy2(source / name, target_dir / name)
        copied.append(f"{DBT_DOCS_DESTINATION}/{name}")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="公開用スナップショットを書き出す（commit・pushはしない）"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="出力先ディレクトリ。既存の場合は中止する"
    )
    parser.add_argument(
        "--dbt-docs",
        type=Path,
        help=(
            "dbt docsのartifactを展開したディレクトリ。"
            f"指定すると公開ツリーの {DBT_DOCS_DESTINATION} へ取り込む"
        ),
    )
    args = parser.parse_args()

    destination: Path = args.out.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"中止しました。出力先が空ではありません: {destination}")

    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if dirty:
        raise SystemExit(
            "中止しました。未コミットの変更があります。"
            "公開するのはHEADのツリーなので、先にコミットしてください。"
        )

    destination.mkdir(parents=True, exist_ok=True)
    included, excluded = export(destination)

    # **秘密の走査より前に取り込む。** 取り込んだ後に走査すれば、dbt docsも同じ検査を通る。
    if args.dbt_docs is not None:
        included.extend(copy_dbt_docs(args.dbt_docs.resolve(), destination))

    findings = scan_for_secrets(destination)
    if findings:
        shutil.rmtree(destination)
        print("秘密を検出したため出力を破棄しました:")
        print("\n".join(findings))
        return 1

    dangling = dangling_references(destination, list(excluded))
    dangling += dangling_imports(destination, list(excluded))
    if dangling:
        shutil.rmtree(destination)
        print("除外したファイルへの参照が残っているため出力を破棄しました:")
        print("\n".join(dangling))
        print("\n  公開ツリーに存在しないものを案内することになります。記述を直してください。")
        return 1

    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()

    print(f"出力しました: {destination}")
    print(f"  元コミット : {head}")
    print(f"  含めた     : {len(included)}ファイル")
    print(f"  除外した   : {len(excluded)}ファイル")
    for relative in excluded:
        print(f"    - {relative}")
    print("  秘密スキャン: 検出なし")
    print()
    print("公開手順（内容を確認してから実行する）:")
    print(f"  cd {destination}")
    print("  git init && git add -A")
    print("  git commit -m 'feat: publish investment data platform MVP'")
    print("  git remote add origin <公開リポジトリのURL>")
    print("  git push -u origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
