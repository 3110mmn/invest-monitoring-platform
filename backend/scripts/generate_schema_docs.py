"""Alembic初期スキーマからドキュメントを生成し、手書き資料との整合性を検査する。

方針は「導出できるものは生成し、意味づけは検査する」。

生成対象:
  - docs/data/schema-reference.md          全体を自動生成（手編集禁止）
  - docs/data/er-diagram.md                ER図のmermaidブロックのみ生成

検査対象（手書きの意味づけを保持したまま、構造との食い違いを検出する）:
  - docs/data/schema-data-dictionary.md のテーブル一覧・列定義

使い方:
    python scripts/generate_schema_docs.py           # 生成して書き込む
    python scripts/generate_schema_docs.py --check   # 差分があれば終了コード1
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.schema_model import Schema, Table, parse_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
# Alembicのmigrationを**全て**順に適用した結果が現在のスキーマになる。1本目だけを
# 見ていると、後から落としたテーブルが文書に残り続ける。
MIGRATIONS_DIR = REPO_ROOT / "backend" / "migrations" / "versions"


def migration_paths() -> list[Path]:
    """適用順のmigrationを返す。ファイル名の連番を順序とする。"""
    return sorted(p for p in MIGRATIONS_DIR.glob("*.py") if not p.name.startswith("__"))
REFERENCE_PATH = REPO_ROOT / "docs" / "data" / "schema-reference.md"
ER_DIAGRAM_PATH = REPO_ROOT / "docs" / "data" / "er-diagram.md"
DICTIONARY_PATH = REPO_ROOT / "docs" / "data" / "schema-data-dictionary.md"

GENERATED_START = "<!-- generated:er-diagram start -->"
GENERATED_END = "<!-- generated:er-diagram end -->"

MARKDOWN_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
BACKTICK_TOKEN = re.compile(r"`([^`]+)`")
SQL_TYPES = r"TEXT|INTEGER|BIGINT|REAL|DOUBLE(?:\s+PRECISION)?|BOOLEAN|DATE|TIME|TIMESTAMPTZ|BLOB|NUMERIC"
TYPE_AND_NULL = re.compile(rf"^({SQL_TYPES})\s*/\s*(NO|YES)$", re.IGNORECASE)
TYPE_ONLY = re.compile(rf"^({SQL_TYPES})$", re.IGNORECASE)


# ---------------------------------------------------------------- 生成


def _format_default(default: str | None) -> str:
    if default is None:
        return "-"
    return f"`{default}`"


def _render_table_section(table: Table, schema: Schema) -> list[str]:
    lines = [f"### `{table.name}`", ""]
    lines.append("| 列 | 型 | NULL | 既定値 | 列挙値 |")
    lines.append("|---|---|---|---|---|")
    for column in table.columns:
        enum = ", ".join(f"`{v}`" for v in column.check_values) if column.check_values else "-"
        lines.append(
            f"| `{column.name}` | {column.type or '-'} | "
            f"{'YES' if column.nullable else 'NO'} | {_format_default(column.default)} | {enum} |"
        )
    lines.append("")

    keys: list[str] = []
    if table.primary_key:
        keys.append("- 主キー: " + ", ".join(f"`{c}`" for c in table.primary_key))
    for unique in table.uniques:
        keys.append("- 一意キー: " + ", ".join(f"`{c}`" for c in unique))
    for fk in table.foreign_keys:
        cols = ", ".join(f"`{c}`" for c in fk.columns)
        refs = ", ".join(f"`{c}`" for c in fk.ref_columns)
        suffix = f"（ON DELETE {fk.on_delete}）" if fk.on_delete else ""
        keys.append(f"- 外部キー: {cols} → `{fk.ref_table}`({refs}){suffix}")
    for check in table.table_checks:
        keys.append(f"- テーブルCHECK: `{check}`")
    for index in schema.indexes_for(table.name):
        cols = ", ".join(f"`{c}`" for c in index.columns)
        kind = "一意インデックス" if index.unique else "インデックス"
        where = f" WHERE `{index.where}`" if index.where else ""
        keys.append(f"- {kind} `{index.name}`: {cols}{where}")
    lines.extend(keys)
    lines.append("")
    return lines


def render_reference(schema: Schema) -> str:
    """schema.sql の構造リファレンス全文を生成する。"""
    lines = [
        "# スキーマ構造リファレンス（自動生成）",
        "",
        "このファイルは `generate_schema_docs.py` が",
        "[初期migration](../../backend/migrations/versions/0001_initial_postgresql.py) から生成します。",
        "**直接編集しないでください。**",
        "",
        "列の意味・由来・運用ルールは [`schema-data-dictionary.md`](schema-data-dictionary.md)、",
        "論理層の設計方針は [`design-principles.md`](design-principles.md) を参照してください。",
        "",
        "## テーブル一覧",
        "",
        "| テーブル | 列数 | 主キー |",
        "|---|---|---|",
    ]
    for table in schema.tables:
        pk = ", ".join(f"`{c}`" for c in table.primary_key) or "-"
        lines.append(f"| `{table.name}` | {len(table.columns)} | {pk} |")
    lines.append("")
    lines.append("## テーブル定義")
    lines.append("")
    for table in schema.tables:
        lines.extend(_render_table_section(table, schema))
    return "\n".join(lines).rstrip() + "\n"


def render_er_diagram(schema: Schema) -> str:
    """外部キーからER図のmermaidブロックを生成する。"""
    lines = ["```mermaid", "erDiagram"]
    for table in schema.tables:
        for fk in table.foreign_keys:
            # 子側の外部キーが子の主キーそのものなら1対0..1、それ以外は1対多。
            cardinality = "||--o|" if fk.columns == table.primary_key else "||--o{"
            label = "_".join(fk.columns)
            lines.append(f"    {fk.ref_table} {cardinality} {table.name} : {label}")
    lines.append("```")
    return "\n".join(lines)


def _replace_generated_block(text: str, block: str, path: Path) -> str:
    start = text.find(GENERATED_START)
    end = text.find(GENERATED_END)
    if start == -1 or end == -1:
        raise SystemExit(
            f"{path} に生成マーカーがありません。"
            f"{GENERATED_START} と {GENERATED_END} で囲んだ範囲を用意してください。"
        )
    head = text[: start + len(GENERATED_START)]
    tail = text[end:]
    return f"{head}\n{block}\n{tail}"


# ---------------------------------------------------------------- 検査


def _table_rows(lines: list[str]) -> list[list[str]]:
    """markdownテーブルの行を、区切り行を除いてセル配列で返す。"""
    rows: list[list[str]] = []
    for line in lines:
        match = MARKDOWN_TABLE_ROW.match(line)
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue
        rows.append(cells)
    return rows


def _first_backticked_cell(cells: list[str]) -> tuple[int, list[str]] | None:
    for index, cell in enumerate(cells):
        tokens = BACKTICK_TOKEN.findall(cell)
        if tokens:
            return index, tokens
    return None


def _section_lines(lines: list[str], predicate) -> list[str]:
    """見出しが条件を満たす節の本文行を返す。"""
    collected: list[str] = []
    capturing = False
    current_level = 0
    for line in lines:
        heading = re.match(r"^(#{2,4})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            if capturing and level <= current_level:
                capturing = False
            if predicate(heading.group(2)):
                capturing = True
                current_level = level
                continue
        if capturing:
            collected.append(line)
    return collected


def _enumerated_tables(lines: list[str], heading_keyword: str) -> set[str]:
    section = _section_lines(lines, lambda h: heading_keyword in h)
    names: set[str] = set()
    for cells in _table_rows(section):
        found = _first_backticked_cell(cells)
        if found:
            names.update(found[1])
    return names


def _check_table_enumeration(
    label: str, documented: set[str], schema_tables: set[str]
) -> list[str]:
    problems = []
    for missing in sorted(schema_tables - documented):
        problems.append(f"{label}: `{missing}` がスキーマに存在するが記載されていない")
    for extra in sorted(documented - schema_tables):
        problems.append(f"{label}: `{extra}` が記載されているがスキーマに存在しない")
    return problems


def _check_dictionary_columns(schema: Schema, lines: list[str]) -> list[str]:
    problems: list[str] = []
    documented_sections: set[str] = set()

    current_table: str | None = None
    section_rows: dict[str, list[list[str]]] = {}
    for line in lines:
        heading = re.match(r"^###\s+`([A-Za-z_][A-Za-z0-9_]*)`\s*$", line)
        if heading:
            current_table = heading.group(1)
            documented_sections.add(current_table)
            section_rows[current_table] = []
            continue
        if re.match(r"^#{1,3}\s+", line):
            current_table = None
            continue
        if current_table:
            section_rows[current_table].extend(_table_rows([line]))

    schema_tables = set(schema.table_names)
    for extra in sorted(documented_sections - schema_tables):
        problems.append(f"データ定義書: `{extra}` の節があるがスキーマに存在しない")
    for missing in sorted(schema_tables - documented_sections):
        problems.append(f"データ定義書: `{missing}` の節がない")

    for table_name in sorted(documented_sections & schema_tables):
        table = schema.table(table_name)
        assert table is not None
        documented_columns: set[str] = set()
        for cells in section_rows[table_name]:
            found = _first_backticked_cell(cells)
            if not found:
                continue
            name_index, tokens = found
            columns = [t for t in tokens if re.fullmatch(r"[a-z_][a-z0-9_]*", t)]
            if not columns:
                continue
            documented_columns.update(columns)

            type_cell = cells[name_index + 1] if name_index + 1 < len(cells) else ""
            type_match = TYPE_AND_NULL.match(type_cell) or TYPE_ONLY.match(type_cell)
            if not type_match:
                continue
            documented_type = type_match.group(1).upper()
            documented_null = type_match.group(2).upper() if type_match.re is TYPE_AND_NULL else None
            for column_name in columns:
                column = table.column(column_name)
                if column is None:
                    continue
                if column.type and column.type != documented_type:
                    problems.append(
                        f"データ定義書 `{table_name}.{column_name}`: 型が "
                        f"{documented_type} と記載されているがスキーマは {column.type}"
                    )
                if documented_null is not None:
                    actual_null = "YES" if column.nullable else "NO"
                    if documented_null != actual_null:
                        problems.append(
                            f"データ定義書 `{table_name}.{column_name}`: NULL可否が "
                            f"{documented_null} と記載されているがスキーマは {actual_null}"
                        )

        for missing in sorted(set(table.column_names) - documented_columns):
            problems.append(f"データ定義書 `{table_name}`: 列 `{missing}` の記載がない")
        for extra in sorted(documented_columns - set(table.column_names)):
            problems.append(
                f"データ定義書 `{table_name}`: 列 `{extra}` が記載されているがスキーマに存在しない"
            )
    return problems


def check_dictionary(schema: Schema) -> list[str]:
    """手書きのデータ定義書とスキーマの食い違いを列挙する。"""
    lines = DICTIONARY_PATH.read_text(encoding="utf-8").splitlines()
    schema_tables = set(schema.table_names)
    problems: list[str] = []
    problems += _check_table_enumeration(
        "データ定義書 データ由来一覧",
        _enumerated_tables(lines, "データ由来一覧"),
        schema_tables,
    )
    problems += _check_table_enumeration(
        "データ定義書 テーブル一覧と粒度",
        _enumerated_tables(lines, "テーブル一覧と粒度"),
        schema_tables,
    )
    problems += _check_dictionary_columns(schema, lines)

    er_lines = ER_DIAGRAM_PATH.read_text(encoding="utf-8").splitlines()
    problems += _check_table_enumeration(
        "ER図 テーブル一覧",
        _enumerated_tables(er_lines, "テーブル一覧"),
        schema_tables,
    )
    return problems


# ---------------------------------------------------------------- 実行


def build_outputs(schema: Schema) -> dict[Path, str]:
    """生成対象ファイルのパスと期待内容を返す。"""
    er_text = ER_DIAGRAM_PATH.read_text(encoding="utf-8")
    return {
        REFERENCE_PATH: render_reference(schema),
        ER_DIAGRAM_PATH: _replace_generated_block(
            er_text, render_er_diagram(schema), ER_DIAGRAM_PATH
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Alembicスキーマからドキュメントを生成・検査する")
    parser.add_argument("--check", action="store_true", help="書き込まず差分の有無だけ判定する")
    args = parser.parse_args()

    schema = parse_schema(migration_paths())
    outputs = build_outputs(schema)
    problems = check_dictionary(schema)

    stale: list[Path] = []
    for path, content in outputs.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"生成: {path.relative_to(REPO_ROOT)}")

    failed = False
    if stale:
        failed = True
        print("生成物が古くなっています。`python scripts/generate_schema_docs.py` を実行してください:")
        for path in stale:
            print(f"  - {path.relative_to(REPO_ROOT)}")
    if problems:
        failed = True
        print("手書きドキュメントとスキーマの不一致:")
        for problem in problems:
            print(f"  - {problem}")
    if not failed:
        print("スキーマとドキュメントは一致しています")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
