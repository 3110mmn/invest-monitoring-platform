# データ取得元と利用方針

採用する外部データ、代替候補、保存・再配布・失敗時の共通動作を定めます。

個別API項目からDB列への変換はコードとテストを正本とし、この文書では管理しません。

記載の数値は末尾の確認日時点で実測または公式資料で確認したもの。プラン変更や仕様変更で
変わるため、変更時は本書を更新する。

## 1. テーブル × 取得元

外部から取得するテーブルのみを対象とする。ユーザー入力・ETL内部生成のテーブル
（`theme`, `data_source`, `ingestion_run` 等）は
[データ定義書 §2](schema-data-dictionary.md)を参照。

| テーブル | 採用中 | 代替・候補 | 状態 |
|---|---|---|---|
| `market_price_observation` | **yfinance**（管理銘柄の直近） | J-Quants `/equities/bars/daily`（全市場、84日前まで） | 期間で分担。[§2](#2-株価の取得元)を参照 |
| `reference/security_master` Parquet | **J-Quants `/equities/master`** | — | 全上場銘柄。名称・市場・17/33業種を保持 |
| `investment_target` | 手動登録 | スクリーナーからの追加 | PostgreSQLには監視対象だけを保持 |
| `investment_target_identifier` | **J-Quants `/equities/master`** | — | `jpx_code` のみ。将来 `edinet_code` を追加 |
| `financial_disclosure` / `financial_summary` | **J-Quants `/fins/summary`** | EDINET DB API、EDINET API | 実装済み。代替は未着手 |
| （マクロ指標） | 未実装 | FRED | 利用要件の確定後に選定 |
| （ベンチマーク指数） | 未実装 | J-Quants TOPIX、yfinance | 分析要件と契約プランの確定後に選定 |

## 2. 株価の取得元

全上場銘柄マスタは`jquants_sync.py archive-master`でrawへ保存し、
`build_security_master.py`で`lake/reference/security_master`へ変換します。全件を
`investment_target`へロードしません。スクリーナーの候補集合はParquet、ユーザーが選んだ
監視対象だけがPostgreSQLという境界を維持します。

### 優先順位

1. **J-Quants** — 調整済みOHLCで、`ingestion_run` による来歴が残る
2. **yfinance** — J-Quantsが提供しない直近期間を埋める代替

### 役割分担

Freeプランは直近84日を提供しない。一方この84日は、アプリで表示する銘柄にとっては最も
見たい期間である。そこで**期間で分担する**。

| 担当 | 取得元 | 対象 | 1日あたりのリクエスト |
|---|---|---|---|
| 84日前より過去（全市場） | J-Quants `archive-prices --catch-up` | 全銘柄（約4,700） | 1 |
| 直近84日 | yfinance `daily_update.py` | 管理銘柄のみ | 銘柄数ぶん |

**この空白は埋まらない。** J-Quantsが提供する最新日は毎日1日ずつ進むが、今日も同じだけ
進むため、84日の窓は平行移動し続ける。初回に84日分をまとめて埋めた後も、毎日1日ぶんの
yfinance取得が要る。

代わりに、**yfinanceで埋めた日には84日後にJ-Quantsの公式版が届く**。Derived側が
J-Quantsを優先するため（`backend/analytics/derived/daily_return.sql`）、暫定値は自動的に
公式値へ入れ替わる。yfinanceの行は削除しない。一次観測を上書きしないという方針どおり、
「その時点で何を見て判断したか」も残す。

### yfinanceを全銘柄へ広げない理由

yfinanceは1銘柄1リクエストで、対象は `investment_target` に限る。全銘柄へ広げない。

- 非公式エンドポイントであり、一括取得の許諾がない
- 全銘柄ぶんを毎日送るとレート制限か遮断に当たる
- 必要がない。全市場の網羅はJ-Quantsの担当で、直近84日が要るのは表示する銘柄だけ

管理銘柄が増えるとリクエスト数は線形に増える。数十件を超える場合は、`yf.download` の
複数ティッカー一括取得へ切り替えて1リクエストへまとめる。

### 比較

| 観点 | J-Quants | yfinance |
|---|---|---|
| 位置づけ | 公式API（JPX） | 非公式ライブラリ（Yahoo Financeの非公開エンドポイント） |
| 速報性 | 当日。**ただしFreeは直近84日を提供しない** | 当日 |
| 取得可能期間 | プラン依存。Freeは2年ローリング窓 | 長期。遡って取得できる |
| 価格調整 | 調整方法が公開されている | `auto_adjust=True` で遡及調整。**過去の値が後日変わる** |
| 来歴 | `ingestion_run` / `ingestion_error` を記録 | **記録しない**（`ingestion_run_id` は NULL） |
| レート制限 | プラン依存（Free 5回/分） | 明示なし |
| 継続性 | 契約に基づく | 提供側の変更で停止しうる |

yfinanceは可用性で選んでいるのであって、品質で優る訳ではない。上位プランへ変更した時点で
日次をJ-Quantsへ寄せ、yfinanceはフォールバックに戻す。

## 3. 財務データの取得元

### 優先順位

1. **J-Quants `/fins/summary`** — 採用中。決算短信ベースで開示単位、訂正履歴と会社予想を含む
2. **EDINET DB API**（第三者） — 詳細科目・有報テキストが必要になった場合の候補
3. **EDINET API**（金融庁） — 原本が必要な場合の候補。一次情報

短信と有報は別の開示であり、置き換えではなく補完関係にある。短信は決算発表当日、
有報は決算日から3か月以内の提出で、**速報性は短信が上**。

### 比較

| 観点 | J-Quants `/fins/summary` | EDINET DB API | EDINET API |
|---|---|---|---|
| 一次情報 | 決算短信（TDnet） | 有価証券報告書 | 有価証券報告書 |
| 提供元 | JPX | 第三者サービス | 金融庁（一次） |
| 返るもの | **正規化済みJSON** | 正規化済みの科目・指標 | **XBRL / PDF / CSV の書類** |
| 粒度 | 開示単位。訂正開示を別レコードで保持 | 企業×会計期 | 書類単位 |
| 速報性 | 短信当日。**Freeは12週遅延** | EDINET反映の翌日（8:00 JST） | EDINET反映と同時 |
| 期間 | プラン依存。Freeは2年 | FY2020〜FY2025 | **10年で削除される** |
| 詳細科目 | `/fins/details` は**Premium専用** | Free から利用可 | XBRL全科目（要解析） |
| 認証 | APIキー | APIキー | APIキー（要登録） |
| 制限 | Free 5回/分 | Free 100回/日、Pro ¥4,980/月 | 公式な明示なし |
| 実装コスト | 低（正規化済み） | 低〜中 | **高（XBRL解析が必要）** |

### 切り替え・追加の判断基準

| 状況 | 取るべき手段 |
|---|---|
| 直近の決算が見たい | **J-Quantsの上位プランへ変更**。取得元を増やすより変更範囲が小さい |
| BS/PL/CFの詳細科目が必要 | EDINET系を検討。J-Quants `/fins/details` はPremium専用で割高 |
| 開示原本が必要（監査・再現） | EDINET API。ただし**10年を経過した書類は削除される**ため、必要なら早めに取り込む |
| 有報テキスト・ESG・政策保有株式 | EDINET DB API |

取得元を追加してもスキーマ変更は不要。`financial_disclosure` の一意キーは
`(source_id, disclosure_number)` なので、同じ決算をJ-Quants由来とEDINET由来で並存できる。
価格の `(target_id, source_id, obs_date)` と同じ考え方。

## 4. 取得元を追加するときの手順

1. 本書の比較表に候補を追加し、既存の採用元と比較する
2. 取得元の利用条件・保存・再配布の可否を確認する
3. `data_source` に登録し、`terms_url` を設定する
4. 銘柄の識別子が必要なら `investment_target_identifier` に `identifier_type` を追加する
5. 既存テーブルへ流し込む場合、既存データを上書きしない粒度になっているかを確認する

## 確認日

| 内容 | 確認日 | 方法 |
|---|---|---|
| J-Quantsの提供期間・レート制限・`/fins/summary` の項目 | 2026-09-14 | 実レスポンス |
| EDINET APIのキー要否・10年削除 | 2026-09-14 | 実リクエストと公式仕様書 |
| EDINET DB APIの料金・カバー範囲 | 2026-09-14 | 提供元サイト |
| `^TNX` / `1343.T` / `^N225` / `1306.T` の取得可否 | 2026-09-13〜14 | yfinanceで実取得 |

## 保存

- rawレスポンスはGCSの非公開バケットへイミュータブルに保存し、`ingestion_run.raw_path`から
  追跡します。保存済みの同じキーへ再実行する場合は、SHA-256が一致するときだけ成功とします。
- 正規化後のWeb Serving FactはPostgreSQLへ保存します。
- 全市場・長期履歴が必要になった場合も、PostgreSQLへ無条件に集約せず、保存価値を確認して
  GCS Parquetまたは外部APIからの再取得を選択します。
- **GCS Parquetは分析入力であり、正本ではありません。** rawまたはPostgreSQLから再生成できる
  状態を保ちます。正本を増やすと、食い違ったときにどれが正しいか判断できなくなるためです。
- **財務開示と株価は、どちらも蓄積しますが理由が違います。** 財務開示は契約プランの提供期間を
  過ぎると取得できず、訂正前の値も残らないため、失うと取り返せません。株価は再取得できますが、
  調整済み価格は分割で遡及して変わるため、`price_basis`と共に保持します。
- 各取得元の規約URLは`data_source.terms_url`に保持します。

### PBR計算に使う価格

PBRなどの1株指標では、調整済み終値だけでは財務開示のBPSと株数基準が揃いません。そのため
J-Quants価格Parquetには、チャートとリターンに使う調整済み`close_price`に加えて、
`raw_close_price`と`adjustment_factor`を保持します。`point_in_time_pbr`は、価格日より前に公表済みの
最新BPSを採用し、期末後の調整係数をBPSへ反映してから未調整終値と比較します。同日開示は時刻の
前後を日付だけで確定できないため、翌取引日から利用可能とする保守的な規則です。

日足の全プラン標準項目は列数が少なく、流動性、時価総額、コーポレートアクション等へ再利用する
可能性が高いため、Observed Parquetで次を型付き保存します。Premium限定の前場・後場項目は、
取得できない段階ではスキーマへ先行追加しません。

| 区分 | Parquet列 |
|---|---|
| 未調整値 | `raw_open_price`, `raw_high_price`, `raw_low_price`, `raw_close_price`, `raw_volume` |
| 調整済み値 | `open_price`, `high_price`, `low_price`, `close_price`, `volume`, `price_basis` |
| 売買・規模 | `turnover_value`, `market_cap_million_yen` |
| 権利・値幅制限 | `adjustment_factor`, `ex_rights_type`, `upper_limit_flag`, `lower_limit_flag` |
| 来歴 | `source_key`, `ingestion_run_id`, `built_at` |

yfinanceのrawには未調整終値とJ-Quants互換の調整係数を保存していないため、実データのPBRは
J-Quants価格でのみ計算します。公開用synthetic demoは分割なし・調整係数1として同じ定義を通します。
欠損、非正のBPS、調整係数の履歴不足は0で補完せず、`calculation_status`へ理由を残します。
`is_below_book_value`は`0 < PBR < 1`というDerivedの事実フラグであり、割安かどうかの評価は
Assessmentで行います。

## 再配布

取得データは再配布しません。J-Quants APIは個人の私的利用に限定され、取得データの第三者配信や、
そのデータを利用したアプリの第三者提供は営利・非営利を問わず禁止されています。正確な適用条件は
[J-Quants公式サイトのFAQ](https://jpx-jquants.com/)を正とします（確認日: 2026-09-23）。

| 利用先 | 使用するデータ | 公開可否 |
|---|---|---|
| 非公開ETL・分析環境 | 契約範囲内の外部取得データ | 非公開 |
| 非公開Admin API | 契約範囲内の外部取得データ | 非公開 |
| 公開Frontend / Public API | synthetic dataのみ | 公開可 |
| README・ポートフォリオ画像 | synthetic dataまたは公開許諾済みデータのみ | 公開可 |

実データのPostgreSQL、GCS、Admin APIは非公開とし、リポジトリにもDB、raw、バックアップを含めません。

## 取得制御

### レート制限

- `JQUANTS_REQUESTS_PER_MINUTE`から送信間隔を導きます。既定値はFreeプランの5回/分です。
- HTTP 429は`Retry-After`があればその秒数、なければ制限窓が明けるまで待ちます。
- 5xxは指数バックオフで再試行します。

### 提供期間

J-Quantsの契約プランは取得期間を制限します。範囲外を含むリクエスト全体がHTTP 400になるため、
`JQUANTS_HISTORY_LAG_DAYS`と`JQUANTS_HISTORY_YEARS`から利用可能な窓を求め、要求期間を事前に
収めます。上位プランへ変更した場合はこの2値も更新します。

### タイムアウト

HTTPタイムアウトは`JQUANTS_TIMEOUT_SECONDS`で設定します。タイムアウトは再試行対象とし、
最終的に失敗した対象は部分失敗の方針に従います。

## 部分失敗

回復可能な1対象の失敗で処理全体を止めず、取得できた分を保存して次回再試行します。

| 事象 | 扱い |
|---|---|
| 一部銘柄の取得失敗 | 残りを継続し、`ingestion_error`へ記録。実行状態は`partial` |
| 全銘柄の取得失敗 | 実行状態は`failed`。正本を更新しない |
| 銘柄マスタ同期の失敗 | 価格取得を継続し、次回実行で識別子を再作成 |
| 検証の失敗 | 壊れたデータを保存せず処理を失敗させる |

判断基準は「次回実行で回復できるものは止めない、正本を壊すものは止める」です。

## 日次更新とバックフィル

日次更新は取得元ごとの最終観測日を起点にし、訂正や確定遅れを拾うため直近数日を重ねて取得します。
取得元に観測がない銘柄の過去分は、日次更新ではなくバックフィルで初期投入します。

| 状況 | 担当 |
|---|---|
| 前回実行からの差分、停止期間の穴埋め | 日次更新 |
| 銘柄や取得元を追加したときの過去分 | バックフィル |
