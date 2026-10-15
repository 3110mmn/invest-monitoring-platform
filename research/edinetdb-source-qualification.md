# EDINET DB Source Qualification

## 結論

EDINET DBを、小売業の過去財務backfill元として**条件付き採用**する。

Raw responseは欠損・単体値・他ソースとの差異を含めて改変せず保存する。Curated層では、
連結区分、会計基準、原本の`doc_id`、提出日時、NULL理由、値の概念とversionを品質属性として保持する。

検証日は2026-10-11。小売業の売上高上位20社について、企業ごとの直近10年度を科目充足率の
評価期間とした。API responseと企業別明細は再配布せず、Git管理外の
`research/outputs/edinetdb_source_qualification/`へ保存している。

## 採用ゲート

| 項目 | 実測 | 基準 | 判定 |
|---|---:|---:|---|
| 3年以上連続する企業 | 100.00% | 80% | PASS |
| 売上高充足率 | 98.48% | 90% | PASS |
| 売上原価充足率 | 88.83% | 70% | PASS |
| 販管費充足率 | 96.45% | 70% | PASS |
| 営業利益充足率 | 97.46% | 90% | PASS |

全取得期間を分母にすると、古い年度の詳細科目欠損により営業利益充足率は79.30%になる。
これを隠さないため、全期間の充足率は`full_history_field_coverage.csv`へ別途出力する。

## 連結区分・会計基準・来歴

直近10年度の197行は次の構成だった。

| 属性 | 行数 | 割合 |
|---|---:|---:|
| 連結 | 193 | 97.97% |
| 単体 | 2 | 1.02% |
| 連結区分不明 | 2 | 1.02% |
| 日本基準 | 185 | 93.91% |
| IFRS | 12 | 6.09% |
| `doc_id`・提出日・原本URLあり | 191 | 96.95% |

来歴欠損6行は、コア財務値が無い2行と、トライアルHDの連結比較可能性が低い古い4行に集中した。
Rawでは削除せず、Curated層で分析対象外または要確認として扱う。

## J-Quantsとの照合

売上高・営業利益について比較可能だった80件中77件が完全一致し、3件が不一致だった。

### U.S.M.H.の売上高2件

EDINET DBは売上高、J-Quantsの決算短信サマリーは営業収益を採っていた。同じ決算資料内に
異なる科目として併存するため、データ誤りではなく概念差である。

- FY2025: 売上高793,986百万円、営業収益811,273百万円
- FY2026: 売上高944,425百万円、営業収益963,762百万円

したがって共通の`revenue`だけで照合せず、`net_sales`と`operating_revenue`等の原概念を保持する。

### バローHD FY2025の営業利益1件

- J-QuantsおよびFY2025有価証券報告書: 23,191百万円
- EDINET DBの年度行: 23,173百万円
- FY2026決算短信に掲載された前年比較値: 23,173百万円

後続資料で採択された比較値がEDINET DBへ反映された可能性がある。単純な誤抽出や
「速報値対確定値」と断定せず、当初開示値と後続採択値を別versionとして保持する。
EDINET DBの年度行だけでは訂正前後の全versionを再現できないため、point-in-time分析では
J-Quantsの開示日時付き当初値を優先する。

## Phase 2Bの取込条件

1. GCS RawにはAPI responseを改変せず保存する。
2. `source`、endpoint、取得日時、response hash、API versionをmanifestへ保存する。
3. 財務行には対象年度と`available_at`、`fetched_at`を分離して持たせる。
4. `basis`、会計基準、`doc_id`、提出日時、NULL理由を保持する。
5. 科目の共通名だけでなく、売上高・営業収益等の原概念を保持する。
6. J-QuantsとEDINET DBの値を上書き統合せず、source別Observationとして併存させる。
7. Curated層の優先規則は、速報再現と最新比較の用途別に定義する。
8. 原本不一致またはversion不明の値は品質フラグを付け、Trigger入力へ自動採用しない。

## 再現方法

```bash
set -a
source .env
set +a

python -m research.edinetdb_source_qualification \
  --industry retail \
  --sample-size 20 \
  --jquants-parquet data/parquet
```

取得済みresponseがある場合はローカルキャッシュから再集計され、APIリクエストを消費しない。
`--refresh`はAPI responseを取り直す場合だけ使用する。

## 参照

- [EDINET DB API仕様](https://edinetdb.jp/docs/api)
- [J-Quants 財務情報API](https://jpx.gitbook.io/j-quants-pro-ja/api-reference/statements)
- [U.S.M.H. 2025年2月期決算短信](https://www.usmh.co.jp/wp-content/themes/usmh/files/20250410/Ust_20250410_1540.pdf)
- [バローHD 2025年3月期有価証券報告書](https://valorholdings.co.jp/wp-content/uploads/published/wp_re_yuho202503.pdf)
- [バローHD 2026年3月期決算短信](https://valorholdings.co.jp/wp-content/uploads/published/wp_re_69_tanshin_20260513.pdf)
