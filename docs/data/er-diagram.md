# ER Diagram

投資判断プラットフォームの現行 DB スキーマを表す ER 図です。

スキーマ定義の正本は [Alembic migration](../../backend/migrations/versions/) です。

## ER 図

<!-- generated:er-diagram start -->
```mermaid
erDiagram
    strategy ||--o{ theme : strategy_id
    data_source ||--o{ ingestion_run : source_id
    ingestion_run ||--o{ ingestion_error : ingestion_run_id
    investment_target ||--o{ investment_target_identifier : target_id
    data_source ||--o{ investment_target_identifier : source_id
    theme ||--o{ theme_investment_target : theme_id
    investment_target ||--o{ theme_investment_target : target_id
```
<!-- generated:er-diagram end -->

## テーブル一覧

| テーブル | 説明 |
|---|---|
| `strategy` | 投資戦略マスタ |
| `theme` | 投資テーマ。`strategy_id`で戦略に所属 |
| `investment_target` | 投資対象マスタ（個別株・ETF・投資信託・REIT・債券・指数・商品） |
| `data_source` | J-Quants、FRED等のデータ取得先 |
| `investment_target_identifier` | 内部銘柄とJ-Quants Code、ticker等の対応 |
| `ingestion_run` | データ取得・ETLの実行履歴と件数 |
| `ingestion_error` | 実行中の部分失敗と再試行可否 |
| `theme_investment_target` | テーマ×銘柄（basket_weight） |

## 役割別構成

```mermaid
flowchart TB
    subgraph Master[Master / Dimension-like]
        strategy
        theme
        investment_target
        data_source
    end

    subgraph Relationship[Relationship]
        theme_investment_target
        investment_target_identifier
    end

    subgraph Observed["Observed Facts（GCS Parquet）"]
        market_price["observed/market_price"]
        financial["observed/financial_summary"]
    end

    subgraph Provenance[Ingestion Provenance]
        ingestion_run
        ingestion_error
    end

    strategy --> theme
    theme --> theme_investment_target
    investment_target --> theme_investment_target
    investment_target --> investment_target_identifier
    data_source --> investment_target_identifier
    investment_target -.->|"target_key"| market_price
    investment_target -.->|"target_key"| financial
    ingestion_run -.->|"ingestion_run_id"| market_price
    ingestion_run -.->|"ingestion_run_id"| financial
    data_source --> ingestion_run
    ingestion_run --> ingestion_error
```
