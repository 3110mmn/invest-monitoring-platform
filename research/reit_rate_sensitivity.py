"""用途分類別のJ-REIT Total Returnと10年JGB利回り変化を探索する。

分析はResearch専用。入力CSV・分析結果をPostgreSQL、GCS、APIへ書き込まない。

**価格はParquet lakeではなくyfinanceから取る。** lakeはJ-Quants Freeプランの2年窓しか
持たず、週次に落とすと106点しか残らないため用途分類別の感応度を検定できない。yfinanceは
58銘柄すべてで2005年まで遡り、`Adj Close`が分配金再投資を織り込むのでTotal Returnが作れる。
分配金を含まない価格リターンだけでは、インカムが金利上昇を緩衝するかを見られない。

ただしyfinanceの調整値は遡及して変わるため、取得時刻を添えてローカルへキャッシュし、
一次観測としては扱わない。キャッシュも分析結果もGit管理外である。
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = PROJECT_ROOT / "research" / "inputs"
OUTPUT_DIR = PROJECT_ROOT / "research" / "outputs" / "reit_rate_sensitivity"
PRICE_CACHE_PATH = INPUT_DIR / "reit_prices_yfinance.csv"
PRICE_COLUMNS = ["jpx_code", "obs_date", "close_price", "adjusted_close_price"]
ASSET_TYPES = {
    "office",
    "retail",
    "residential",
    "logistics",
    "hotel",
    "healthcare",
    # 総合型（3用途以上）と複合型（2用途）を分ける。畳むと58銘柄中30件が1群になり、
    # 用途別の金利感応度を見る目的に合わない。
    "diversified",
    "composite",
    "other",
}
ASSET_COLUMNS = {
    "jpx_code",
    "asset_type",
    "source_label",
    "classification_as_of",
    "source_name",
    "source_url",
}


def normalize_jpx_code(value: Any) -> str:
    """4桁の表示コードまたはJ-Quantsの5桁コードを正規化する。

    **英字を含むコードを受け付ける。** JPXは2024年から英数字の銘柄コードを発行しており、
    霞ヶ関ホテルリート（表示`401A`）は銘柄マスタで`401A0`、`target_key`は`401A.T`である。
    数字だけを前提にすると、新しい上場分が黙って分析から落ちる。
    """
    code = str(value).strip().upper()
    if code.endswith(".0"):
        code = code[:-2]
    if len(code) == 4 and code.isalnum() and code[0].isdigit():
        return f"{code}0"
    if len(code) == 5 and code.isalnum() and code[0].isdigit() and code.endswith("0"):
        return code
    raise ValueError(f"J-REITの証券コード形式ではありません: {value!r}")


def read_asset_types(path: Path) -> pd.DataFrame:
    """手元で作成した用途分類を検証して読む。"""
    frame = pd.read_csv(path, dtype={"jpx_code": "string"})
    missing = ASSET_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"用途分類CSVに必要な列がありません: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("用途分類CSVに1行以上入力してください")

    frame["jpx_code"] = frame["jpx_code"].map(normalize_jpx_code)
    frame["asset_type"] = frame["asset_type"].astype("string").str.strip().str.lower()
    unknown = sorted(set(frame["asset_type"].dropna()) - ASSET_TYPES)
    if unknown:
        raise ValueError(f"未対応のasset_typeです: {', '.join(unknown)}")
    if frame["asset_type"].isna().any():
        raise ValueError("asset_typeが空の行があります")
    if frame["jpx_code"].duplicated().any():
        duplicates = sorted(frame.loc[frame["jpx_code"].duplicated(), "jpx_code"].unique())
        raise ValueError(f"証券コードが重複しています: {', '.join(duplicates)}")

    frame["classification_as_of"] = pd.to_datetime(
        frame["classification_as_of"], errors="raise"
    ).dt.date
    if frame["classification_as_of"].isna().any():
        raise ValueError("classification_as_ofが空の行があります")
    for column in ("source_label", "source_name", "source_url"):
        frame[column] = frame[column].astype("string").str.strip()
        if frame[column].isna().any() or frame[column].eq("").any():
            raise ValueError(f"{column}が空の行があります")
    return frame


def read_mof_jgb_csv(path: Path) -> pd.DataFrame:
    """財務省の利回りCSVからDate・10Yのみを抽出する。"""
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))

    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "Date" in [cell.strip() for cell in row]
            and "10Y" in [cell.strip() for cell in row]
        ),
        None,
    )
    if header_index is None:
        raise ValueError("財務省CSV内にDate・10Y列のヘッダーがありません")

    header = [cell.strip() for cell in rows[header_index]]
    date_index, yield_index = header.index("Date"), header.index("10Y")
    observations: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        if max(date_index, yield_index) >= len(row):
            continue
        raw_date, raw_yield = row[date_index].strip(), row[yield_index].strip()
        if not raw_date or not raw_yield or raw_yield == "-":
            continue
        try:
            observed_date = datetime.strptime(raw_date, "%Y/%m/%d").date()
            yield_pct = float(raw_yield)
        except ValueError:
            continue
        observations.append({"date": observed_date, "yield_pct": yield_pct})

    if not observations:
        raise ValueError("財務省CSVから有効な10年利回りを読み取れませんでした")
    frame = pd.DataFrame(observations).sort_values("date")
    if frame["date"].duplicated().any():
        raise ValueError("財務省CSVに同じ日付の10年利回りが複数あります")
    return frame.reset_index(drop=True)


def to_yahoo_symbol(jpx_code: str) -> str:
    """J-Quantsの5桁コードをYahoo Financeのティッカーへ変換する。

    5桁コードは4桁の表示コードへ`0`を付けたものなので、末尾を落として`.T`を付ける。
    英数字コードも同じ規則で、`401A0`は`401A.T`である。
    """
    if len(jpx_code) != 5 or not jpx_code.endswith("0"):
        raise ValueError(f"5桁のJ-Quantsコードではありません: {jpx_code!r}")
    return f"{jpx_code[:-1]}.T"


def fetch_reit_prices(codes: list[str], *, max_workers: int = 4) -> pd.DataFrame:
    """yfinanceから全期間の終値と分配金再投資済み終値を取得する。

    `auto_adjust=False`で`Close`と`Adj Close`の両方を受け取る。`Close`は分割調整済みだが
    分配金を含まず、`Adj Close`は分配金再投資込みである。両方を残すことで、価格リターンと
    Total Returnの金利感応度を比べられる。

    1銘柄の失敗で全体を止めない。取得できた銘柄だけを返し、欠けたコードは呼び出し側が
    報告する。これは非公式エンドポイントであり、全銘柄の取得を前提にできないためである。
    """
    import yfinance as yf

    def fetch_one(code: str) -> pd.DataFrame:
        try:
            history = yf.Ticker(to_yahoo_symbol(code)).history(
                period="max", auto_adjust=False
            )
        except Exception as error:  # 1銘柄の失敗で分析全体を止めない
            print(f"警告: {code} の取得に失敗しました: {error}", file=sys.stderr)
            return pd.DataFrame(columns=PRICE_COLUMNS)
        if history.empty:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        return pd.DataFrame(
            {
                "jpx_code": code,
                "obs_date": history.index.tz_localize(None).normalize(),
                "close_price": history["Close"].to_numpy(),
                "adjusted_close_price": history["Adj Close"].to_numpy(),
            }
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        frames = [frame for frame in executor.map(fetch_one, codes) if not frame.empty]
    if not frames:
        raise ValueError("yfinanceからREIT価格を1銘柄も取得できませんでした")
    prices = pd.concat(frames, ignore_index=True)
    prices["fetched_at"] = datetime.now(UTC).isoformat()
    return prices.sort_values(["jpx_code", "obs_date"]).reset_index(drop=True)


def load_reit_prices(
    codes: list[str], *, cache_path: Path, refresh: bool, max_workers: int = 4
) -> pd.DataFrame:
    """キャッシュがあればそれを読み、無いか`refresh`なら取得して保存する。

    **既定でキャッシュを使う。** 58銘柄の全期間取得は非公式エンドポイントへ58リクエストを
    送るため、分析条件を変えるたびに取り直さない。調整値は遡及して変わるので`fetched_at`を
    残し、いつの時点のスナップショットかを分かる状態にする。
    """
    if not refresh and cache_path.exists():
        cached = pd.read_csv(cache_path, dtype={"jpx_code": "string"})
        missing = set(PRICE_COLUMNS) - set(cached.columns)
        if missing:
            raise ValueError(
                f"価格キャッシュに必要な列がありません: {', '.join(sorted(missing))}。"
                "--refresh で取り直してください"
            )
        if "fetched_at" in cached.columns and not cached["fetched_at"].empty:
            print(f"価格キャッシュを使用: {cache_path}（取得時刻 {cached['fetched_at'].iloc[0]}）")
        return cached

    prices = fetch_reit_prices(codes, max_workers=max_workers)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(cache_path, index=False)
    print(f"yfinanceから取得し保存: {cache_path}（{len(prices):,}行）")
    return prices


def drop_corrupt_prices(
    daily: pd.DataFrame, *, max_ratio: float = 5.0, window: int = 5
) -> pd.DataFrame:
    """前後の水準から桁が外れた終値を落とす。

    yfinanceの長期履歴には単発の壊れた価格が混じる。実例として8951.Tの2007-07-06は
    終値2,690円で、前後の営業日はいずれも175,000円前後だった。週次に落とすと
    −98%と+6,368%の対になり、officeの年率ボラティリティが199%に膨らんでいた。

    **閾値は桁落ちだけを捕まえる水準に置く。** 中心5営業日の中位値に対して5倍以上または
    1/5以下の点だけを落とす。J-REITの1日の値動きがこの幅に達することはなく、分割は
    `Close`・`Adj Close`の両方で調整済みなので、2008年の−45%や2020年の−42%といった
    実際の暴落は残る。

    落とした行は黙って捨てずに報告する。何件を除いたか分からないまま回帰を回すと、
    データ不具合と市場の事実を区別できなくなる。
    """
    if max_ratio <= 1:
        raise ValueError("max_ratioは1より大きい必要があります")
    frame = daily.sort_values(["jpx_code", "obs_date"]).copy()
    reference = frame.groupby("jpx_code", sort=False)["close_price"].transform(
        lambda series: series.rolling(window, center=True, min_periods=3).median()
    )
    ratio = frame["close_price"] / reference
    corrupt = ratio.gt(max_ratio) | ratio.lt(1 / max_ratio)
    if corrupt.any():
        for _, row in frame.loc[corrupt].iterrows():
            print(
                f"警告: 前後の水準から外れた終値を除外しました: "
                f"{row['jpx_code']} {pd.Timestamp(row['obs_date']).date()} "
                f"close={row['close_price']:,.1f}",
                file=sys.stderr,
            )
    return frame.loc[~corrupt].reset_index(drop=True)


def build_weekly_panel(
    prices: pd.DataFrame,
    asset_types: pd.DataFrame,
    yields: pd.DataFrame,
) -> pd.DataFrame:
    """価格とJGB利回りを週次でas-of結合し、用途分類別の分析入力を作る。"""
    missing = set(PRICE_COLUMNS) - set(prices.columns)
    if missing:
        raise ValueError(f"価格データに必要な列がありません: {', '.join(sorted(missing))}")
    if prices.empty:
        raise ValueError("分類対象のREIT価格がありません")

    daily = prices.copy()
    daily["jpx_code"] = daily["jpx_code"].map(normalize_jpx_code)
    daily["obs_date"] = pd.to_datetime(daily["obs_date"], errors="raise")
    for column in ("close_price", "adjusted_close_price"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
        daily = daily[daily[column] > 0]
    daily = daily.dropna(subset=["close_price", "adjusted_close_price"])
    daily = drop_corrupt_prices(daily)
    daily["week"] = daily["obs_date"].dt.to_period("W-FRI")
    weekly = (
        daily.sort_values("obs_date")
        .groupby(["jpx_code", "week"], as_index=False, sort=True)
        .tail(1)
        .sort_values(["jpx_code", "week"])
        .reset_index(drop=True)
    )
    weekly["week_ordinal"] = weekly["week"].map(lambda period: period.ordinal)
    by_security = weekly.groupby("jpx_code", sort=False)
    weekly["previous_close"] = by_security["close_price"].shift()
    weekly["previous_adjusted_close"] = by_security["adjusted_close_price"].shift()
    weekly["previous_week_ordinal"] = by_security["week_ordinal"].shift()
    consecutive_week = weekly["week_ordinal"] - weekly["previous_week_ordinal"] == 1
    weekly["price_return"] = weekly["close_price"] / weekly["previous_close"] - 1
    # **Total Returnは`Adj Close`から作る。** 分配金再投資込みの系列なので、権利落ちによる
    # 下落が収益の喪失として混ざらない。価格リターンと並べることでインカムの寄与が見える。
    weekly["total_return"] = (
        weekly["adjusted_close_price"] / weekly["previous_adjusted_close"] - 1
    )
    weekly.loc[~consecutive_week, ["price_return", "total_return"]] = np.nan

    yield_rows = yields.copy()
    yield_rows["date"] = pd.to_datetime(yield_rows["date"], errors="raise")
    yield_rows["yield_pct"] = pd.to_numeric(yield_rows["yield_pct"], errors="coerce")
    yield_rows = yield_rows.dropna(subset=["yield_pct"]).sort_values("date")
    if yield_rows.empty:
        raise ValueError("10年利回りデータが空です")

    # **日時の解像度を揃える。** 取得元によって`obs_date`はマイクロ秒や秒になり、財務省CSVを
    # `to_datetime`で読んだ`date`と解像度が違うと`merge_asof`が
    # 「incompatible merge keys」で落ちる。どちらも日付しか意味を持たないので日単位へ正規化する。
    weekly["obs_date"] = pd.to_datetime(weekly["obs_date"]).dt.normalize().astype("datetime64[ns]")
    yield_rows["date"] = yield_rows["date"].dt.normalize().astype("datetime64[ns]")

    weekly = pd.merge_asof(
        weekly.sort_values("obs_date"),
        yield_rows,
        left_on="obs_date",
        right_on="date",
        direction="backward",
        tolerance=pd.Timedelta(days=7),
    )
    weekly["yield_age_days"] = (weekly["obs_date"] - weekly["date"]).dt.days
    weekly = weekly.sort_values(["jpx_code", "week"]).reset_index(drop=True)
    weekly["previous_yield_pct"] = weekly.groupby("jpx_code", sort=False)["yield_pct"].shift()
    weekly["yield_change_bp"] = (weekly["yield_pct"] - weekly["previous_yield_pct"]) * 100
    weekly = weekly.merge(
        asset_types[
            [
                "jpx_code",
                "asset_type",
                "source_label",
                "classification_as_of",
                "source_name",
                "source_url",
            ]
        ],
        on="jpx_code",
        how="inner",
        validate="many_to_one",
    )
    if weekly["yield_pct"].isna().any():
        raise ValueError("利回りCSVと価格週次データを7日以内で結合できない週があります")
    return weekly.drop(
        columns=[
            "week_ordinal",
            "previous_week_ordinal",
            "previous_close",
            "previous_adjusted_close",
            "previous_yield_pct",
        ]
    )


def summarize_asset_types(panel: pd.DataFrame, *, hac_maxlags: int = 4) -> pd.DataFrame:
    """等ウェイト週次Total Returnと10年金利変化への感応度を要約する。

    感応度はTotal Returnで測る。価格リターンだけでは、分配金の権利落ちによる下落が
    金利感応度として混ざる。価格リターンの平均は併記し、インカムの寄与を見えるようにする。
    """
    if hac_maxlags < 0:
        raise ValueError("hac_maxlagsは0以上である必要があります")
    valid = panel.dropna(subset=["total_return", "price_return", "yield_change_bp"])
    weekly = (
        valid.groupby(["asset_type", "week"], as_index=False)
        .agg(
            weekly_total_return=("total_return", "mean"),
            weekly_price_return=("price_return", "mean"),
            yield_change_bp=("yield_change_bp", "mean"),
            security_count=("jpx_code", "nunique"),
        )
        .sort_values(["asset_type", "week"])
    )
    if weekly.empty:
        raise ValueError("有効な週次リターンと利回り変化がありません")

    summaries: list[dict[str, Any]] = []
    for asset_type, group in weekly.groupby("asset_type", sort=True):
        returns = group["weekly_total_return"].astype(float)
        model_data = group[["weekly_total_return", "yield_change_bp"]].dropna()
        row: dict[str, Any] = {
            "asset_type": asset_type,
            "observations": len(model_data),
            "securities_min": int(group["security_count"].min()),
            "securities_max": int(group["security_count"].max()),
            "mean_weekly_total_return": float(returns.mean()),
            "mean_weekly_price_return": float(group["weekly_price_return"].astype(float).mean()),
            "annualized_volatility": float(returns.std(ddof=1) * np.sqrt(52)),
            "cumulative_total_return": float((1 + returns).prod() - 1),
            "max_drawdown": float(((1 + returns).cumprod() / (1 + returns).cumprod().cummax() - 1).min()),
            "beta_return_pct_per_100bp": np.nan,
            "beta_ci95_low": np.nan,
            "beta_ci95_high": np.nan,
            "beta_p_value": np.nan,
            "regression_r_squared": np.nan,
        }
        if len(model_data) >= 12 and model_data["yield_change_bp"].nunique() > 1:
            import statsmodels.api as sm

            y = model_data["weekly_total_return"].astype(float)
            x = sm.add_constant(model_data["yield_change_bp"].astype(float))
            maxlags = min(hac_maxlags, max(len(model_data) // 4, 0))
            fit = sm.OLS(y, x).fit(
                cov_type="HAC",
                cov_kwds={"maxlags": maxlags},
            )
            beta = float(fit.params["yield_change_bp"])
            interval = fit.conf_int().loc["yield_change_bp"]
            row.update(
                {
                    "beta_return_pct_per_100bp": beta * 100,
                    "beta_ci95_low": float(interval.iloc[0]) * 100,
                    "beta_ci95_high": float(interval.iloc[1]) * 100,
                    "beta_p_value": float(fit.pvalues["yield_change_bp"]),
                    "regression_r_squared": float(fit.rsquared),
                }
            )
        summaries.append(row)
    return pd.DataFrame(summaries)


def run_analysis(
    *,
    asset_types_path: Path,
    mof_csv_path: Path,
    output_dir: Path,
    cache_path: Path = PRICE_CACHE_PATH,
    refresh: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    asset_types = read_asset_types(asset_types_path)
    yields = read_mof_jgb_csv(mof_csv_path)
    codes = asset_types["jpx_code"].tolist()
    prices = load_reit_prices(codes, cache_path=cache_path, refresh=refresh)
    found_codes = set(prices["jpx_code"].map(normalize_jpx_code)) if not prices.empty else set()
    missing_codes = sorted(set(asset_types["jpx_code"]) - found_codes)
    if missing_codes:
        print(f"警告: 価格を取得できなかったコード: {', '.join(missing_codes)}", file=sys.stderr)
    panel = build_weekly_panel(prices, asset_types, yields)
    summary = summarize_asset_types(panel)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "weekly_panel.csv", index=False)
    summary.to_csv(output_dir / "asset_type_summary.csv", index=False)
    print(f"週次パネル: {len(panel):,}行 / {panel['jpx_code'].nunique()}銘柄")
    print(
        f"分析期間: {panel['obs_date'].min().date()} 〜 {panel['obs_date'].max().date()} "
        "（Total Return。yfinanceのAdj Closeによる分配金再投資込み）"
    )
    print(summary.to_string(index=False))
    print(f"結果はローカル専用ディレクトリへ保存しました: {output_dir}")
    return panel, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-types", type=Path, default=INPUT_DIR / "reit_asset_types.csv")
    parser.add_argument("--mof-csv", type=Path, default=INPUT_DIR / "jgbcme_all.csv")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--price-cache", type=Path, default=PRICE_CACHE_PATH)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="価格キャッシュを無視してyfinanceから取り直す",
    )
    args = parser.parse_args()
    run_analysis(
        asset_types_path=args.asset_types,
        mof_csv_path=args.mof_csv,
        output_dir=args.out,
        cache_path=args.price_cache,
        refresh=args.refresh,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
