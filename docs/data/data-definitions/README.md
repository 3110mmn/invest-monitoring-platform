# Data Definitions

このディレクトリは、保存先ごとの物理データ定義をまとめます。

| 文書 | 対象 | 正本との関係 |
|---|---|---|
| [PostgreSQLデータ定義](postgresql.md) | Control Planeの列の意味・由来・運用ルール | Alembicが物理スキーマの正本 |
| [PostgreSQL構造リファレンス](postgresql-schema-reference.md) | 型・NULL可否・キー・制約・ER図 | Alembicから自動生成。直接編集禁止 |
| [Parquetデータ定義](parquet.md) | Data PlaneのMaster / Observed列と変換規則 | PyArrow schemaとnormalizerが物理定義の正本 |
| [BigQueryデータ定義](bigquery.md) | 分析履歴のraw / stagingとlineage | loaderが発行するDDLが物理定義の正本 |

PostgreSQLとParquetの責務分離は[Data Design Principles](../design-principles.md)を参照します。
