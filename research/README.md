# Research

DuckDB + Notebookで横断・時系列の探索分析を行う領域です。ここで得た候補や結論を、そのままAPI、
Assessment、Decisionへ保存しません。継続監視する価値が確認できた項目だけを後続実装へ昇格させます。

## PBR Research Pilot

PBR 1倍割れが、低収益性・財務健全性・キャッシュ創出力でどこまで説明できるかを探索します。

### セットアップ

```bash
python -m pip install -r backend/requirements-research.txt
gcloud auth application-default login  # GCSを読む場合の初回だけ
```

### 起動

```bash
PARQUET_LAKE=data/parquet jupyter lab research/notebooks/pbr_value_trap_pilot.ipynb
```

GCSの最新lakeを使う場合は`PARQUET_LAKE=gs://<bucket>/lake`を指定します。Notebook内の
`AS_OF_DATE`は固定値で、実行日の最新値へ暗黙に切り替わりません。

## 責務

- `sql/`: 母集団と候補Evidenceの再現可能な計算
- `notebooks/`: 仮説、品質確認、分析、解釈、限界、後続への引き渡し
- `outputs/`: 一時生成物。Git管理しない

SQLとNotebookはObserved / Derivedを読み取るだけです。PostgreSQL、GCS mart、FastAPIへ書き込みません。
