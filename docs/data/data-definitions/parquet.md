# Parquetデータ定義書

本書は、GCS `lake/`配下のParquetデータセットの粒度、列の意味、取得元と変換規則を
定義します。

Parquetは分析用の派生物であり、原本ではありません。取得時のrawをGCSに保存し、
Parquetはrawと変換コードから再生成します。

## 正本と更新ルール

| 対象 | 物理スキーマの正本 | 変換の正本 |
|---|---|---|
| `reference/security_master` | `SECURITY_MASTER_SCHEMA` | `build_security_master.py` |
| `observed/market_price` | `PRICE_SCHEMA` | `normalize_jquants_price()` / `build_yfinance_rows()` |
| `observed/financial_summary` | `DISCLOSURE_FIELDS` + `build_schema()` | `normalize_jquants_financial()` |

スキーマと文書が食い違う場合は、`backend/scripts/build_*.py`のPyArrow schemaと
`backend/app/etl/normalizers.py`を正とします。列の追加・改名・型変更時は本書も更新します。

## 共通規約

- 欠損・未提供値は`NULL`とし、0や空文字で補完しない
- PostgreSQLの代理キーに依存せず、`jpx_code`、`target_key`、`source_key`等のbusiness keyを保持する
- `ingestion_run_id`はrawを取得した実行、`built_at`はParquetへ変換した日時を表す
- J-Quants由来とyfinance由来の観測は`source_key`で区別し、一次観測を上書きしない
- 物理ファイルはzstd圧縮する
- `year=YYYY`は価格の`obs_date`、財務の`disclosed_date`から作るHive partitionであり、
  Parquetファイル内の物理列ではない

## データセット一覧

| パス | 区分 | 1行の粒度 | 主な取得元 |
|---|---|---|---|
| `reference/security_master` | Master | 1上場証券の最新マスタ状態 | J-Quants `/equities/master` |
| `observed/market_price/year=YYYY` | Observed | 1証券 × 1観測日 × 1取得元 | J-Quants `/equities/bars/daily`、yfinance |
| `observed/financial_summary/year=YYYY` | Observed | 1取得元 × 1開示番号 | J-Quants `/fins/summary` |

## `reference/security_master`

全上場証券を検索する参照マスタ。全件をPostgreSQLの`investment_target`へ複製せず、
ユーザーが選択した対象だけをControl Planeへ登録します。

| Parquet列 | PyArrow型 | J-Quants元項目 | 意味・変換 |
|---|---|---|---|
| `security_key` | string | `Code` | 証券のbusiness key。現在はJPXコード |
| `jpx_code` | string | `Code` | J-Quantsの5桁コード |
| `target_key` | string | `Code` | 標準5桁コードのみ`72030` → `7203.T`。一意に変換できない場合はNULL |
| `company_name` | string | `CoName` | 日本語名 |
| `company_name_english` | string | `CoNameEn` | 英文名 |
| `product_category_code` | string | `ProdCat` | J-Quants商品区分コード |
| `market_code` | string | `Mkt` | 市場コード |
| `market_name` | string | `MktNm` | 市場名 |
| `sector_17_code` | string | `S17` | 17業種コード |
| `sector_17_name` | string | `S17Nm` | 17業種名 |
| `sector_33_code` | string | `S33` | 33業種コード |
| `sector_33_name` | string | `S33Nm` | 33業種名 |
| `scale_category` | string | `ScaleCat` | 規模区分 |
| `source_key` | string | 内部付与 | `jquants` |
| `source_date` | date32 | `Date` | 取得元マスタの基準日 |
| `ingestion_run_id` | int64 | 内部付与 | raw取得実行ID |
| `built_at` | timestamp(us, UTC) | 内部付与 | Parquet構築日時 |

## `observed/market_price`

J-Quantsとyfinanceの日足を同一スキーマで保持します。同じ取得元・証券・日付を再取得した場合は、
最も新しい`ingestion_run_id`の行を残します。取得元間の優先順位はParquet構築時に決めず、
Derived側で選択します。

| Parquet列 | PyArrow型 | J-Quants元項目 | 意味・変換 |
|---|---|---|---|
| `target_key` | string | `Code` | 標準5桁コードのみ`.T`ティッカーへ変換。yfinanceでは取得対象キー |
| `jpx_code` | string | `Code` | JPXコード。日本証券のyfinance行では`target_key`から導出 |
| `source_key` | string | 内部付与 | `jquants`または`yfinance` |
| `obs_date` | date32 | `Date` | 価格観測日 |
| `raw_open_price` | double | `O` | 未調整始値 |
| `raw_high_price` | double | `H` | 未調整高値 |
| `raw_low_price` | double | `L` | 未調整安値 |
| `raw_close_price` | double | `C` | 未調整終値 |
| `raw_volume` | double | `Vo` | 未調整出来高 |
| `turnover_value` | double | `Va` | 売買代金 |
| `open_price` | double | `AdjO` | 調整済み始値 |
| `high_price` | double | `AdjH` | 調整済み高値 |
| `low_price` | double | `AdjL` | 調整済み安値 |
| `close_price` | double | `AdjC` | 調整済み終値 |
| `volume` | double | `AdjVo` | 調整済み出来高 |
| `price_basis` | string | 内部付与 | 価格基準。現行のDerivedは`adjusted`を優先 |
| `adjustment_factor` | double | `AdjFactor` | 日次調整係数 |
| `market_cap_million_yen` | double | `MktCap` | 時価総額（百万円） |
| `ex_rights_type` | string | `ExRT` | 権利落ち等の種別 |
| `upper_limit_flag` | bool | `UL` | ストップ高フラグ。`0` / `1`をboolean化 |
| `lower_limit_flag` | bool | `LL` | ストップ安フラグ。`0` / `1`をboolean化 |
| `ingestion_run_id` | int64 | 内部付与 | raw取得実行ID。yfinanceの現行経路ではNULLになり得る |
| `built_at` | timestamp(us, UTC) | 内部付与 | Parquet構築日時 |

yfinanceはJ-Quantsと同じ列に整形しますが、J-Quantsと同等の未調整値、調整係数、
権利・値幅制限情報を持たないため、それらの列はNULLになり得ます。

## `observed/financial_summary`

J-Quants `/fins/summary`の全raw項目を複製するデータセットではありません。判断・分析で
再利用するサマリー項目を採用し、型付きの内部名へ正規化します。連結開示は連結項目、
非連結開示は`NC*`項目を同じParquet列へ収納し、`reporting_scope`で区別します。

同じ取得元・開示番号の再取得は最新`ingestion_run_id`を残します。訂正開示は別の
`disclosure_number`として残し、最新だけに畳みません。

### 開示・来歴列

| Parquet列 | PyArrow型 | J-Quants元項目 | 意味・変換 |
|---|---|---|---|
| `jpx_code` | string | `Code` | JPXコード |
| `target_key` | string | `Code` | 標準5桁コードのみ`.T`ティッカーへ変換 |
| `source_key` | string | 内部付与 | `jquants` |
| `disclosure_number` | string | `DiscNo` | 開示番号。取得元内のbusiness key |
| `disclosed_date` | date32 | `DiscDate` | 開示日 |
| `disclosed_time` | string | `DiscTime` | 開示時刻。取得元表現を保持 |
| `document_type` | string | `DocType` | 書類種別 |
| `fiscal_period_type` | string | `CurPerType` | FY / 1Q / 2Q / 3Q等 |
| `period_start` | date32 | `CurPerSt` | 当該期開始日 |
| `period_end` | date32 | `CurPerEn` | 当該期終了日 |
| `fiscal_year_start` | date32 | `CurFYSt` | 会計年度開始日 |
| `fiscal_year_end` | date32 | `CurFYEn` | 会計年度終了日 |
| `accounting_standard` | string | `DocType`から導出 | 会計基準 |
| `reporting_scope` | string | `DocType`から導出 | `consolidated` / `non_consolidated` |
| `source_record_hash` | string | raw全体から導出 | キー順を固定したrawレコードのSHA-256 |
| `ingestion_run_id` | int64 | 内部付与 | raw取得実行ID |
| `built_at` | timestamp(us, UTC) | 内部付与 | Parquet構築日時 |

### 財務数値列

`int64`は金額または株式数、`double`は1株当たり値です。金額の通貨と単位は
J-Quants `/fins/summary`の仕様に従います。

| Parquet列 | 型 | 連結元項目 | 非連結元項目 | 意味 |
|---|---|---|---|---|
| `revenue` | int64 | `Sales` | `NCSales` | 売上高・営業収益 |
| `operating_income` | int64 | `OP` | `NCOP` | 営業利益 |
| `ordinary_income` | int64 | `OdP` | `NCOdP` | 経常利益 |
| `net_income` | int64 | `NP` | `NCNP` | 当期純利益 |
| `total_assets` | int64 | `TA` | `NCTA` | 総資産 |
| `equity` | int64 | `Eq` | `NCEq` | 自己資本 |
| `forecast_revenue` | int64 | `FSales` | `FNCSales` | 当期予想売上高 |
| `forecast_operating_income` | int64 | `FOP` | `FNCOP` | 当期予想営業利益 |
| `forecast_ordinary_income` | int64 | `FOdP` | `FNCOdP` | 当期予想経常利益 |
| `forecast_net_income` | int64 | `FNP` | `FNCNP` | 当期予想純利益 |
| `next_forecast_revenue` | int64 | `NxFSales` | `NxFNCSales` | 翌期予想売上高 |
| `next_forecast_operating_income` | int64 | `NxFOP` | `NxFNCOP` | 翌期予想営業利益 |
| `next_forecast_ordinary_income` | int64 | `NxFOdP` | `NxFNCOdP` | 翌期予想経常利益 |
| `next_forecast_net_income` | int64 | `NxFNp` | `NxFNCNP` | 翌期予想純利益 |
| `eps` | double | `EPS` | `NCEPS` | 1株当たり利益 |
| `bps` | double | `BPS` | `NCBPS` | 1株当たり純資産 |
| `forecast_eps` | double | `FEPS` | `FNCEPS` | 当期予想EPS |
| `next_forecast_eps` | double | `NxFEPS` | `NxFNCEPS` | 翌期予想EPS |
| `operating_cash_flow` | int64 | `CFO` | `CFO` | 営業キャッシュフロー |
| `investing_cash_flow` | int64 | `CFI` | `CFI` | 投資キャッシュフロー |
| `financing_cash_flow` | int64 | `CFF` | `CFF` | 財務キャッシュフロー |
| `cash_equivalents` | int64 | `CashEq` | `CashEq` | 現金及び現金同等物 |
| `shares_outstanding` | int64 | `ShOutFY` | `ShOutFY` | 期末発行済株式数 |
| `treasury_shares` | int64 | `TrShFY` | `TrShFY` | 期末自己株式数 |
| `average_shares` | int64 | `AvgSh` | `AvgSh` | 期中平均株式数 |
| `annual_dividend_per_share` | double | `DivAnn` | `DivAnn` | 年間1株当たり配当 |
| `forecast_annual_dividend_per_share` | double | `FDivAnn` | `FDivAnn` | 予想年間1株当たり配当 |
| `distribution_per_unit` | double | `DivUnit` | `DivUnit` | REITの1口当たり分配金 |
| `next_forecast_distribution_per_unit` | double | `NxFDivUnit` | `NxFDivUnit` | REITの翌期予想1口当たり分配金 |

## J-Quants rawとの差

ParquetはJ-Quantsレスポンスの単純コピーではありません。

1. 列名を内部の`snake_case`へ正規化する
2. 文字列の日付・数値・フラグを明示的な型へ変換する
3. 連結・非連結の取得元列を同じ内部列へ統合する
4. `target_key`、`source_key`、`ingestion_run_id`、`built_at`、`source_record_hash`等の識別・来歴列を追加する
5. 価格はyfinanceと同じスキーマへ統合する
6. 財務はrawの全項目ではなく、normalizerで採用した項目だけを型付きで保持する

rawの未採用項目が後から必要になった場合は、rawからnormalizerとPyArrow schemaを
拡張し、Parquetを再生成します。
