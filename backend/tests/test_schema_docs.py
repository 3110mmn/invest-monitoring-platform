"""Alembic migrationとドキュメントの整合性を検査するテスト。

生成物が古い場合や、手書きのデータ定義書がスキーマとずれた場合に失敗する。
"""

from scripts import generate_schema_docs as docs


def test_generated_docs_are_up_to_date():
    """生成対象ファイルがmigration適用後のスキーマと一致する。"""
    schema = docs.parse_schema(docs.migration_paths())
    for path, expected in docs.build_outputs(schema).items():
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        assert actual == expected, (
            f"{path.relative_to(docs.REPO_ROOT)} が古い。"
            "`python scripts/generate_schema_docs.py` を実行すること"
        )


def test_data_dictionary_matches_schema():
    """手書きのデータ定義書がスキーマのテーブル・列・型と一致する。"""
    schema = docs.parse_schema(docs.migration_paths())
    problems = docs.check_dictionary(schema)
    assert problems == [], "\n".join(problems)


def test_dropped_tables_do_not_survive_in_the_schema_model():
    """後のmigrationで落としたテーブルは構造モデルに残らない。

    1本目のmigrationだけを見ていると、廃止したテーブルが文書に残り続ける。実際に
    価格・財務の観測テーブルを落としたとき、生成物だけが古い姿のままになった。
    """
    schema = docs.parse_schema(docs.migration_paths())

    names = {table.name for table in schema.tables}
    assert not names & {
        "market_price_observation",
        "financial_disclosure",
        "financial_summary",
    }
