"""価格・財務の観測テーブルを削除する。

読み手が居なくなったため落とす。価格と財務はGCS上のParquetから読むようになり
（`app/repositories/price_source.py` / `financial_source.py`）、日次ETLもPostgreSQLへは
書かなくなった。PostgreSQLは「何を監視しているか」だけを持つ。

**消しても失うものは無いことを確認してある。** `market_price_observation` には来歴の
無い行が661件あり、rawからは再生成できなかったが、その `(銘柄, 日付)` は全て
Parquetが持っている。突合では終値の乖離が中央値0.00%、構造検査も通っている。

`down` は用意しない。空のテーブルを作り直せても、中身はrawから作る方が正しい。
Revision ID: 0002_drop_observed_tables
Revises: 0001_initial_postgresql
"""

from alembic import op

revision = "0002_drop_observed_tables"
down_revision = "0001_initial_postgresql"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # financial_summary は financial_disclosure を参照するため先に落とす。
    op.execute("DROP TABLE IF EXISTS financial_summary")
    op.execute("DROP TABLE IF EXISTS financial_disclosure")
    op.execute("DROP TABLE IF EXISTS market_price_observation")


def downgrade() -> None:
    raise NotImplementedError(
        "観測テーブルは復元しません。価格と財務の所在は分析層（Parquet）であり、"
        "必要ならrawから作り直します"
    )
