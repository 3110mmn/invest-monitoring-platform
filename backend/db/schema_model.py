"""Alembic migrationから構造モデルを組み立てるモジュール。

ドキュメント生成と整合性検査の双方がこのモジュールを唯一の入力とする。
SQLの完全なパーサではなく、本プロジェクトのmigrationが使う記法に限定する。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_COLUMN_CONSTRAINT_KEYWORDS = (
    "PRIMARY KEY",
    "FOREIGN KEY",
    "UNIQUE",
    "CHECK",
)


@dataclass
class Column:
    """テーブルの1列。"""

    name: str
    type: str
    not_null: bool
    is_primary_key: bool
    default: str | None
    check_values: list[str] = field(default_factory=list)

    @property
    def nullable(self) -> bool:
        """NULLを許容するか。主キーは常に非NULL扱いとする。"""
        return not (self.not_null or self.is_primary_key)


@dataclass
class ForeignKey:
    """外部キー制約。"""

    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    on_delete: str | None = None


@dataclass
class Table:
    """CREATE TABLE 1件。"""

    name: str
    columns: list[Column]
    primary_key: list[str]
    uniques: list[list[str]]
    foreign_keys: list[ForeignKey]
    table_checks: list[str]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


@dataclass
class Index:
    """CREATE INDEX 1件。"""

    name: str
    table: str
    columns: list[str]
    unique: bool
    where: str | None = None


@dataclass
class Schema:
    """migration内のSQL全体。"""

    tables: list[Table]
    indexes: list[Index]

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def table(self, name: str) -> Table | None:
        for tbl in self.tables:
            if tbl.name == name:
                return tbl
        return None

    def indexes_for(self, table: str) -> list[Index]:
        return [i for i in self.indexes if i.table == table]


def _strip_comments(sql: str) -> str:
    """行コメントを除去する。migrationはブロックコメントを使わない。"""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _split_statements(sql: str) -> list[str]:
    """セミコロン区切りで文へ分割する。文字列リテラル内のセミコロンを考慮する。"""
    statements: list[str] = []
    buffer: list[str] = []
    in_string = False
    for char in sql:
        if char == "'":
            in_string = not in_string
        if char == ";" and not in_string:
            statements.append("".join(buffer))
            buffer = []
            continue
        buffer.append(char)
    if "".join(buffer).strip():
        statements.append("".join(buffer))
    return [s.strip() for s in statements if s.strip()]


def _split_top_level(body: str) -> list[str]:
    """括弧の深さ0のカンマで定義本体を分割する。"""
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_string = False
    for char in body:
        if char == "'":
            in_string = not in_string
        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(buffer).strip())
                buffer = []
                continue
        buffer.append(char)
    if "".join(buffer).strip():
        parts.append("".join(buffer).strip())
    return parts


def _paren_content(text: str, start: int) -> tuple[str, int]:
    """start位置の '(' に対応する閉じ括弧までの中身と終了位置を返す。"""
    assert text[start] == "("
    depth = 0
    in_string = False
    for pos in range(start, len(text)):
        char = text[pos]
        if char == "'":
            in_string = not in_string
        if in_string:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : pos], pos
    raise ValueError(f"閉じ括弧が見つかりません: {text[start:start + 40]!r}")


def _identifier_list(text: str) -> list[str]:
    return [t.strip().strip('"').strip("`") for t in text.split(",") if t.strip()]


def _parse_check_values(definition: str) -> list[str]:
    """CHECK(col IN ('a','b')) 形式から列挙値を取り出す。"""
    match = re.search(r"CHECK\s*\(", definition, re.IGNORECASE)
    if not match:
        return []
    inner, _ = _paren_content(definition, match.end() - 1)
    in_match = re.search(r"\bIN\s*\(", inner, re.IGNORECASE)
    if not in_match:
        return []
    values, _ = _paren_content(inner, in_match.end() - 1)
    return [v.strip().strip("'") for v in values.split(",") if v.strip()]


def _parse_column(definition: str) -> Column:
    tokens = definition.split()
    name = tokens[0].strip('"').strip("`")
    type_match = re.match(
        r'^\s*["`]?[A-Za-z_][A-Za-z0-9_]*["`]?\s+(.+?)'
        r"(?=\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|REFERENCES|UNIQUE|CHECK|GENERATED)\b|$)",
        definition,
        re.IGNORECASE | re.DOTALL,
    )
    type_name = " ".join(type_match.group(1).upper().split()) if type_match else ""
    upper = definition.upper()

    default = None
    default_match = re.search(r"\bDEFAULT\s+", definition, re.IGNORECASE)
    if default_match:
        rest = definition[default_match.end() :].lstrip()
        if rest.startswith("("):
            inner, _ = _paren_content(rest, 0)
            default = f"({inner})"
        else:
            default = rest.split()[0]

    return Column(
        name=name,
        type=type_name,
        not_null="NOT NULL" in upper,
        is_primary_key="PRIMARY KEY" in upper,
        default=default,
        check_values=_parse_check_values(definition),
    )


def _parse_create_table(statement: str) -> Table:
    match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        statement,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"CREATE TABLE を解析できません: {statement[:60]!r}")
    name = match.group(1)
    body, _ = _paren_content(statement, match.end() - 1)

    columns: list[Column] = []
    primary_key: list[str] = []
    uniques: list[list[str]] = []
    foreign_keys: list[ForeignKey] = []
    table_checks: list[str] = []

    for item in _split_top_level(body):
        upper = item.upper()
        if upper.startswith("PRIMARY KEY"):
            inner, _ = _paren_content(item, item.index("("))
            primary_key = _identifier_list(inner)
        elif upper.startswith("UNIQUE"):
            inner, _ = _paren_content(item, item.index("("))
            uniques.append(_identifier_list(inner))
        elif upper.startswith("FOREIGN KEY"):
            open_pos = item.index("(")
            cols_text, close_pos = _paren_content(item, open_pos)
            ref_match = re.search(
                r"REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", item[close_pos:], re.IGNORECASE
            )
            if not ref_match:
                raise ValueError(f"FOREIGN KEY を解析できません: {item!r}")
            ref_start = close_pos + ref_match.end() - 1
            ref_cols_text, ref_close = _paren_content(item, ref_start)
            on_delete = None
            on_delete_match = re.search(
                r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION|SET\s+DEFAULT)",
                item[ref_close:],
                re.IGNORECASE,
            )
            if on_delete_match:
                on_delete = " ".join(on_delete_match.group(1).upper().split())
            foreign_keys.append(
                ForeignKey(
                    columns=_identifier_list(cols_text),
                    ref_table=ref_match.group(1),
                    ref_columns=_identifier_list(ref_cols_text),
                    on_delete=on_delete,
                )
            )
        elif upper.startswith("CHECK"):
            inner, _ = _paren_content(item, item.index("("))
            table_checks.append(" ".join(inner.split()))
        else:
            column = _parse_column(item)
            columns.append(column)
            ref_match = re.search(
                r"REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", item, re.IGNORECASE
            )
            if ref_match:
                ref_columns, ref_close = _paren_content(item, ref_match.end() - 1)
                on_delete_match = re.search(
                    r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION|SET\s+DEFAULT)",
                    item[ref_close:],
                    re.IGNORECASE,
                )
                foreign_keys.append(
                    ForeignKey(
                        columns=[column.name],
                        ref_table=ref_match.group(1),
                        ref_columns=_identifier_list(ref_columns),
                        on_delete=(
                            " ".join(on_delete_match.group(1).upper().split())
                            if on_delete_match
                            else None
                        ),
                    )
                )

    if not primary_key:
        primary_key = [c.name for c in columns if c.is_primary_key]

    return Table(
        name=name,
        columns=columns,
        primary_key=primary_key,
        uniques=uniques,
        foreign_keys=foreign_keys,
        table_checks=table_checks,
    )


def _parse_create_index(statement: str) -> Index:
    match = re.search(
        r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s+ON\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        statement,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"CREATE INDEX を解析できません: {statement[:60]!r}")
    columns_text, close_pos = _paren_content(statement, match.end() - 1)
    where_match = re.search(r"WHERE\s+(.+)$", statement[close_pos:], re.IGNORECASE | re.DOTALL)
    return Index(
        name=match.group(2),
        table=match.group(3),
        columns=_identifier_list(columns_text),
        unique=bool(match.group(1)),
        where=" ".join(where_match.group(1).split()) if where_match else None,
    )


def _migration_sql(path: Path) -> str:
    """Alembic migrationの `upgrade()` にある定数 `op.execute` のSQLを繋げて返す。

    1本目のCREATE TABLEだけを拾っていると、後から足したmigrationのDROPやALTERが
    見えない。`upgrade()` の中だけを見るのは、`downgrade()` のSQLを混ぜないため。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        raise ValueError(f"upgrade() が見つかりません: {path}")

    statements: list[str] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "op"
            and function.attr == "execute"
        ):
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            statements.append(value.value)
    if not statements:
        raise ValueError(f"op.execute のSQLが見つかりません: {path}")
    return ";\n".join(statements)


DROP_TABLE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"`]?([A-Za-z0-9_]+)", re.IGNORECASE
)
RENAME_TABLE = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z0-9_]+)[\"`]?\s+RENAME\s+TO\s+"
    r"[\"`]?([A-Za-z0-9_]+)",
    re.IGNORECASE,
)
RENAME_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z0-9_]+)[\"`]?\s+RENAME\s+COLUMN\s+"
    r"[\"`]?([A-Za-z0-9_]+)[\"`]?\s+TO\s+[\"`]?([A-Za-z0-9_]+)",
    re.IGNORECASE,
)
ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+ADD\s+COLUMN\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
DROP_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+"
    r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
ADD_CHECK_CONSTRAINT = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+"
    r"ADD\s+CONSTRAINT\s+[A-Za-z_][A-Za-z0-9_]*\s+CHECK\s*\(",
    re.IGNORECASE | re.DOTALL,
)
SET_COLUMN_DEFAULT = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+"
    r"ALTER\s+COLUMN\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+SET\s+DEFAULT\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
DROP_CONSTRAINT = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+"
    r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
ADD_FOREIGN_KEY_CONSTRAINT = re.compile(
    r"ALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+"
    r"ADD\s+CONSTRAINT\s+[A-Za-z_][A-Za-z0-9_]*\s+FOREIGN\s+KEY\s*\(",
    re.IGNORECASE | re.DOTALL,
)


def parse_schema(schema_path: Path | str | list[Path]) -> Schema:
    """SQL定義を読み込み、テーブルとインデックスの構造モデルを返す。

    複数のmigrationを渡すと、順に適用した結果を返す。後のmigrationが落としたテーブルは
    含めない。1本目だけを見ていると、廃止したテーブルが文書に残り続ける。
    """
    paths = [Path(p) for p in (schema_path if isinstance(schema_path, list) else [schema_path])]
    tables: list[Table] = []
    indexes: list[Index] = []
    for path in paths:
        source = (
            _migration_sql(path)
            if path.suffix == ".py"
            else path.read_text(encoding="utf-8")
        )
        sql = _strip_comments(source)
        for statement in _split_statements(sql):
            upper = statement.upper()
            if upper.startswith("CREATE TABLE"):
                tables.append(_parse_create_table(statement))
            elif re.match(r"CREATE\s+(UNIQUE\s+)?INDEX", upper):
                indexes.append(_parse_create_index(statement))
            elif upper.startswith("DROP TABLE"):
                match = DROP_TABLE.match(statement)
                if match:
                    dropped = match.group(1)
                    tables = [t for t in tables if t.name != dropped]
                    indexes = [i for i in indexes if i.table != dropped]
            elif upper.startswith("ALTER TABLE"):
                drop_column_match = DROP_COLUMN.match(statement)
                if drop_column_match:
                    table_name, column_name = drop_column_match.groups()
                    table = next((item for item in tables if item.name == table_name), None)
                    if table is not None:
                        table.columns = [candidate for candidate in table.columns if candidate.name != column_name]
                        table.foreign_keys = [
                            fk for fk in table.foreign_keys if column_name not in fk.columns
                        ]
                        table.uniques = [
                            unique for unique in table.uniques if column_name not in unique
                        ]
                        indexes = [
                            index for index in indexes
                            if index.table != table_name or column_name not in index.columns
                        ]
                    continue
                add_column_match = ADD_COLUMN.match(statement)
                if add_column_match:
                    table_name, definition = add_column_match.groups()
                    table = next((item for item in tables if item.name == table_name), None)
                    if table is not None:
                        new_column = _parse_column(definition)
                        table.columns.append(new_column)
                        check_match = re.search(r"\bCHECK\s*\(", definition, re.IGNORECASE)
                        if check_match:
                            check_body, _ = _paren_content(
                                definition, definition.index("(", check_match.start())
                            )
                            table.table_checks.append(" ".join(check_body.split()))
                    continue
                add_check_match = ADD_CHECK_CONSTRAINT.match(statement)
                if add_check_match:
                    table_name = add_check_match.group(1)
                    table = next((item for item in tables if item.name == table_name), None)
                    if table is not None:
                        check_start = statement.upper().find("CHECK")
                        check_body, _ = _paren_content(statement, statement.index("(", check_start))
                        table.table_checks.append(" ".join(check_body.split()))
                        check_values = _parse_check_values(statement[check_start:])
                        column_match = re.search(
                            r"\b([a-z_][a-z0-9_]*)\s+IN\s*\(", check_body, re.IGNORECASE
                        )
                        if check_values and column_match:
                            column = table.column(column_match.group(1))
                            if column is not None:
                                column.check_values = check_values
                    continue
                default_match = SET_COLUMN_DEFAULT.match(statement)
                if default_match:
                    table_name, column_name, default = default_match.groups()
                    table = next((item for item in tables if item.name == table_name), None)
                    column = table.column(column_name) if table is not None else None
                    if column is not None:
                        column.default = " ".join(default.split())
                    continue
                drop_constraint_match = DROP_CONSTRAINT.match(statement)
                if drop_constraint_match:
                    table_name, constraint_name = drop_constraint_match.groups()
                    table = next((item for item in tables if item.name == table_name), None)
                    suffix = "_fkey"
                    prefix = f"{table_name}_"
                    if table is not None and constraint_name.startswith(prefix) and constraint_name.endswith(suffix):
                        column_name = constraint_name[len(prefix) : -len(suffix)]
                        table.foreign_keys = [
                            fk for fk in table.foreign_keys if fk.columns != [column_name]
                        ]
                    continue
                foreign_key_match = ADD_FOREIGN_KEY_CONSTRAINT.match(statement)
                if foreign_key_match:
                    table_name = foreign_key_match.group(1)
                    table = next((item for item in tables if item.name == table_name), None)
                    if table is not None:
                        key_start = statement.upper().find("FOREIGN KEY")
                        columns, close_pos = _paren_content(
                            statement, statement.index("(", key_start)
                        )
                        ref_match = re.search(
                            r"REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                            statement[close_pos:],
                            re.IGNORECASE,
                        )
                        if ref_match:
                            ref_start = close_pos + ref_match.end() - 1
                            ref_columns, ref_close = _paren_content(statement, ref_start)
                            on_delete_match = re.search(
                                r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION|SET\s+DEFAULT)",
                                statement[ref_close:],
                                re.IGNORECASE,
                            )
                            table.foreign_keys.append(ForeignKey(
                                columns=_identifier_list(columns),
                                ref_table=ref_match.group(1),
                                ref_columns=_identifier_list(ref_columns),
                                on_delete=(
                                    " ".join(on_delete_match.group(1).upper().split())
                                    if on_delete_match else None
                                ),
                            ))
                    continue
                column_match = RENAME_COLUMN.match(statement)
                if column_match:
                    table_name, old_name, new_name = column_match.groups()
                    table = next((item for item in tables if item.name == table_name), None)
                    if table is not None:
                        column = table.column(old_name)
                        if column is not None:
                            column.name = new_name
                        table.primary_key = [new_name if c == old_name else c for c in table.primary_key]
                        table.uniques = [
                            [new_name if c == old_name else c for c in unique]
                            for unique in table.uniques
                        ]
                        for index in indexes:
                            if index.table == table_name:
                                index.columns = [
                                    new_name if c == old_name else c for c in index.columns
                                ]
                    continue
                table_match = RENAME_TABLE.match(statement)
                if table_match:
                    old_name, new_name = table_match.groups()
                    table = next((item for item in tables if item.name == old_name), None)
                    if table is not None:
                        table.name = new_name
                    for index in indexes:
                        if index.table == old_name:
                            index.table = new_name
    return Schema(tables=tables, indexes=indexes)
