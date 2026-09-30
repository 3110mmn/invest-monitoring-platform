# スキーマ構造リファレンス（自動生成）

このファイルは `generate_schema_docs.py` が
[初期migration](../../backend/migrations/versions/0001_initial_postgresql.py) から生成します。
**直接編集しないでください。**

列の意味・由来・運用ルールは [`schema-data-dictionary.md`](schema-data-dictionary.md)、
論理層の設計方針は [`design-principles.md`](design-principles.md) を参照してください。

## テーブル一覧

| テーブル | 列数 | 主キー |
|---|---|---|
| `strategy` | 7 | `strategy_id` |
| `theme` | 8 | `theme_id` |
| `investment_target` | 9 | `target_id` |
| `data_source` | 8 | `source_id` |
| `ingestion_run` | 16 | `ingestion_run_id` |
| `ingestion_error` | 9 | `error_id` |
| `investment_target_identifier` | 10 | `investment_target_identifier_id` |
| `theme_investment_target` | 7 | `membership_id` |

## テーブル定義

### `strategy`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `strategy_id` | BIGINT | NO | `AS` | - |
| `strategy_key` | TEXT | NO | - | - |
| `strategy_name` | TEXT | NO | - | - |
| `description` | TEXT | YES | - | - |
| `is_active` | BOOLEAN | NO | `TRUE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `strategy_id`

### `theme`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `theme_id` | BIGINT | NO | `AS` | - |
| `theme_key` | TEXT | NO | - | - |
| `theme_name` | TEXT | NO | - | - |
| `strategy_id` | BIGINT | NO | - | - |
| `description` | TEXT | YES | - | - |
| `is_active` | BOOLEAN | NO | `TRUE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `theme_id`
- 外部キー: `strategy_id` → `strategy`(`strategy_id`)

### `investment_target`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `target_id` | BIGINT | NO | `AS` | - |
| `target_key` | TEXT | NO | - | - |
| `target_name` | TEXT | NO | - | - |
| `target_type` | TEXT | YES | - | `individual_stock`, `etf`, `mutual_fund`, `reit`, `bond`, `index`, `commodity` |
| `market` | TEXT | YES | - | - |
| `currency` | TEXT | YES | - | - |
| `is_monitored` | BOOLEAN | NO | `TRUE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `target_id`

### `data_source`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `source_id` | BIGINT | NO | `AS` | - |
| `source_key` | TEXT | NO | - | - |
| `source_name` | TEXT | NO | - | - |
| `base_url` | TEXT | YES | - | - |
| `terms_url` | TEXT | YES | - | - |
| `is_active` | BOOLEAN | NO | `TRUE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `source_id`

### `ingestion_run`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `ingestion_run_id` | BIGINT | NO | `AS` | - |
| `job_type` | TEXT | NO | - | - |
| `git_commit_sha` | TEXT | YES | - | - |
| `source_id` | BIGINT | NO | - | - |
| `status` | TEXT | NO | - | `running`, `succeeded`, `partial`, `failed` |
| `requested_from` | DATE | YES | - | - |
| `requested_to` | DATE | YES | - | - |
| `target_count` | INTEGER | NO | `0` | - |
| `fetched_count` | INTEGER | NO | `0` | - |
| `loaded_count` | INTEGER | NO | `0` | - |
| `skipped_count` | INTEGER | NO | `0` | - |
| `failed_count` | INTEGER | NO | `0` | - |
| `raw_path` | TEXT | YES | - | - |
| `error_message` | TEXT | YES | - | - |
| `started_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `finished_at` | TIMESTAMPTZ | YES | - | - |

- 主キー: `ingestion_run_id`
- 外部キー: `source_id` → `data_source`(`source_id`)
- インデックス `idx_ingestion_run_status`: `status`, `started_at`

### `ingestion_error`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `error_id` | BIGINT | NO | `AS` | - |
| `ingestion_run_id` | BIGINT | NO | - | - |
| `entity_type` | TEXT | YES | - | - |
| `entity_key` | TEXT | YES | - | - |
| `stage` | TEXT | NO | - | - |
| `error_type` | TEXT | YES | - | - |
| `error_message` | TEXT | NO | - | - |
| `retryable` | BOOLEAN | NO | `FALSE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `error_id`
- 外部キー: `ingestion_run_id` → `ingestion_run`(`ingestion_run_id`)
- インデックス `idx_ingestion_error_run`: `ingestion_run_id`

### `investment_target_identifier`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `investment_target_identifier_id` | BIGINT | NO | `AS` | - |
| `target_id` | BIGINT | NO | - | - |
| `source_id` | BIGINT | NO | - | - |
| `identifier_type` | TEXT | NO | - | - |
| `identifier` | TEXT | NO | - | - |
| `valid_from` | DATE | NO | `DATE` | - |
| `valid_to` | DATE | YES | - | - |
| `is_primary` | BOOLEAN | NO | `FALSE` | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `investment_target_identifier_id`
- 一意キー: `source_id`, `identifier_type`, `identifier`, `valid_from`
- 外部キー: `target_id` → `investment_target`(`target_id`)
- 外部キー: `source_id` → `data_source`(`source_id`)
- テーブルCHECK: `valid_to IS NULL OR valid_to >= valid_from`
- インデックス `idx_investment_target_identifier_target`: `target_id`
- インデックス `idx_investment_target_identifier_lookup`: `source_id`, `identifier_type`, `identifier`

### `theme_investment_target`

| 列 | 型 | NULL | 既定値 | 列挙値 |
|---|---|---|---|---|
| `membership_id` | BIGINT | NO | `AS` | - |
| `theme_id` | BIGINT | NO | - | - |
| `target_id` | BIGINT | NO | - | - |
| `effective_from` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `effective_to` | TIMESTAMPTZ | YES | - | - |
| `created_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |
| `updated_at` | TIMESTAMPTZ | NO | `CURRENT_TIMESTAMP` | - |

- 主キー: `membership_id`
- 外部キー: `theme_id` → `theme`(`theme_id`)
- 外部キー: `target_id` → `investment_target`(`target_id`)
- テーブルCHECK: `effective_to IS NULL OR effective_to >= effective_from`
- 一意インデックス `uq_theme_target_current_membership`: `theme_id`, `target_id` WHERE `effective_to IS NULL`
- インデックス `idx_theme_target_membership_history`: `theme_id`, `target_id`, `effective_from`
