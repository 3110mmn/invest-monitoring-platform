"""個人情報漏洩の公表が株価に与えた影響を、イベントスタディとして記述する。

分析はResearch専用。入力CSV・分析結果をPostgreSQL、GCS、APIへ書き込まない。

**これは推論ではなく記述である。** n=6では平均CARの信頼区間も「底は平均N営業日」も
主張できない。出力は「この6件ではこう動いた」という事例集であり、統計的な一般化を
意図しない。将来nが増えたとき同じコードで再計算できることを優先する。

## なぜベンチマーク調整が要るか

「公表後に下がった」だけでは、市場全体が下がったのか当該企業が下がったのかを区別できない。
したがって市場リターンを差し引いた異常リターン（Abnormal Return）を見る。

```
AR_t  = 個別銘柄のリターン − β × ベンチマークのリターン
CAR   = ARの累積
```

**βは推定窓（イベント前）で推定する。** イベント窓で推定すると、漏洩の影響がβへ混入する。

## このデータの既知の弱点

6件中3件に設計上の問題があり、`independence`列へ記録している。

| 種別 | 内容 |
|---|---|
| `contaminated` | 同一事案が同業他社へ波及（KDDI）。セクター共通の動きが混入する |
| `follow_up` | 続報であり初報が別日にある（さくら）。市場の初回反応は初報の可能性 |
| `third_party` | 委託先経由で自社システムではない（大和証券） |

これらを平均へ混ぜない。群別に表示し、解釈は読み手へ委ねる。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "research" / "inputs"
OUTPUT_DIR = PROJECT_ROOT / "research" / "outputs" / "breach_event_study"

EVENT_COLUMNS = {"symbol", "company_name", "event_date", "independence"}

# 推定窓はイベント前の平常時。直前を避けるのは、公表前に情報が漏れている場合に
# βが汚染されるため。
ESTIMATION_START = -250
ESTIMATION_END = -31
EVENT_START = -10
EVENT_END = 60


def read_events(path: Path) -> pd.DataFrame:
    """イベント定義を検証して読む。"""
    frame = pd.read_csv(path, dtype={"jpx_code": "string"})
    missing = EVENT_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"イベントCSVに必要な列がありません: {', '.join(sorted(missing))}")
    frame["event_date"] = pd.to_datetime(frame["event_date"], errors="raise")
    if frame["event_date"].isna().any():
        raise ValueError("event_dateに日付でない行があります")
    return frame.sort_values("event_date").reset_index(drop=True)


# 日次リターンの標準偏差がこれを超える系列は、分割の調整漏れを疑う。
# 日本株の指数・個別株で日次5%を超える標準偏差は通常ありえない。
MAX_PLAUSIBLE_DAILY_STD = 0.05


def fetch_prices(symbols: list[str], *, benchmark: str) -> pd.DataFrame:
    """yfinanceから調整済み終値を取得し、日次リターンへ変換する。

    **取得した系列が壊れていないか検査する。** yfinanceの`Adj Close`は分割を遡及調整
    するが、調整漏れが実在する。実例として`1306.T`（TOPIX連動ETF）は2024年の分割が
    反映されておらず、日次リターンの標準偏差が0.433、最大+947%という値になっていた。
    これをベンチマークに使うとβがほぼ0に潰れ、異常リターンが「個別銘柄の生リターン」に
    なってしまう。`1321.T`も同様だった。黙って通さず、ここで落とす。
    """
    import yfinance as yf

    frames: dict[str, pd.Series] = {}
    for symbol in [*symbols, benchmark]:
        if symbol in frames:
            continue
        history = yf.Ticker(symbol).history(period="5y", auto_adjust=False)
        if history.empty:
            raise ValueError(f"{symbol} の価格を取得できませんでした")
        close = history["Adj Close"]
        close.index = close.index.tz_localize(None).normalize()
        frames[symbol] = close

    prices = pd.DataFrame(frames).sort_index()
    returns = prices.pct_change().dropna(how="all")

    for symbol in returns.columns:
        deviation = float(returns[symbol].std())
        if deviation > MAX_PLAUSIBLE_DAILY_STD:
            raise ValueError(
                f"{symbol} の日次リターン標準偏差が{deviation:.3f}で異常です。"
                "分割の調整漏れを疑ってください（1306.T・1321.Tで実例あり）"
            )
    return returns


def market_model(
    returns: pd.DataFrame, symbol: str, benchmark: str, event_date: pd.Timestamp
) -> dict[str, Any] | None:
    """1イベントについて市場モデルでARとCARを計算する。

    推定窓でOLSによりα・βを求め、イベント窓の異常リターンを出す。
    推定窓のサンプルが足りない場合はNoneを返す（黙って粗い推定をしない）。
    """
    if symbol not in returns.columns:
        return None
    pair = returns[[symbol, benchmark]].dropna()
    if event_date not in pair.index:
        # 公表日が非営業日なら直後の営業日を起点にする
        later = pair.index[pair.index >= event_date]
        if later.empty:
            return None
        event_date = later[0]

    position = pair.index.get_loc(event_date)
    est = pair.iloc[max(position + ESTIMATION_START, 0) : position + ESTIMATION_END]
    if len(est) < 100:
        return None

    x = est[benchmark].to_numpy(dtype=float)
    y = est[symbol].to_numpy(dtype=float)
    design = np.column_stack([np.ones_like(x), x])
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha, beta = float(coefficients[0]), float(coefficients[1])

    # **市場モデルがどれだけ説明しているかを残す。** R²が低いと、異常リターンの大半が
    # 「市場で説明できない個別要因」であり、その中にイベント以外のノイズも入る。
    # R²を見ずにCARだけ読むと、説明力の無いモデルの残差を効果と誤読する。
    fitted = alpha + beta * x
    residual = y - fitted
    total = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - float((residual**2).sum()) / total if total > 0 else float("nan")
    # 残差の標準偏差は、CARがノイズの範囲かを判断する目安になる。
    residual_std = float(residual.std(ddof=2))

    window = pair.iloc[max(position + EVENT_START, 0) : position + EVENT_END + 1].copy()
    window["day"] = range(EVENT_START, EVENT_START + len(window))
    window["abnormal"] = window[symbol] - (alpha + beta * window[benchmark])
    window["car"] = window["abnormal"].cumsum()
    window["event_day"] = event_date
    return {
        "alpha": alpha,
        "beta": beta,
        "r_squared": r_squared,
        "residual_std": residual_std,
        "estimation_days": len(est),
        "window": window.reset_index(names="date"),
    }


def run_analysis(
    *, events_path: Path, output_dir: Path, benchmark: str = "1348.T"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = read_events(events_path)
    returns = fetch_prices(events["symbol"].tolist(), benchmark=benchmark)

    panels: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        result = market_model(returns, event["symbol"], benchmark, event["event_date"])
        if result is None:
            print(f"警告: {event['company_name']} は推定窓が足りず除外", file=sys.stderr)
            continue
        window = result["window"]
        window["company_name"] = event["company_name"]
        window["independence"] = event["independence"]
        panels.append(window)

        post = window[window["day"] >= 0]
        if post.empty:
            continue
        trough = post.loc[post["car"].idxmin()]
        latest = post.iloc[-1]
        # CARがノイズの範囲かを見る目安。残差が独立なら累積の標準偏差は√日数倍になる。
        # **これは検定ではない。** n=6で有意性は主張しないが、桁感は必要。
        days_elapsed = int(latest["day"]) + 1
        noise_band_pct = result["residual_std"] * np.sqrt(days_elapsed) * 100
        summaries.append(
            {
                "company_name": event["company_name"],
                "symbol": event["symbol"],
                "event_date": window["event_day"].iloc[0].date(),
                "independence": event["independence"],
                "beta": result["beta"],
                "r_squared": result["r_squared"],
                "residual_std_pct": result["residual_std"] * 100,
                "estimation_days": result["estimation_days"],
                "noise_band_1sd_pct": noise_band_pct,
                "car_day0_pct": float(post.iloc[0]["car"]) * 100,
                "car_day5_pct": float(post[post["day"] == 5]["car"].iloc[0]) * 100
                if (post["day"] == 5).any()
                else np.nan,
                "car_day20_pct": float(post[post["day"] == 20]["car"].iloc[0]) * 100
                if (post["day"] == 20).any()
                else np.nan,
                "trough_day": int(trough["day"]),
                "trough_car_pct": float(trough["car"]) * 100,
                "latest_day": int(latest["day"]),
                "latest_car_pct": float(latest["car"]) * 100,
            }
        )

    if not summaries:
        raise ValueError("計算できたイベントがありません")
    panel = pd.concat(panels, ignore_index=True)
    summary = pd.DataFrame(summaries)

    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "event_panel.csv", index=False)
    summary.to_csv(output_dir / "event_summary.csv", index=False)

    print(f"ベンチマーク: {benchmark}  推定窓 {ESTIMATION_START}〜{ESTIMATION_END}日")
    print(f"イベント: {len(summary)}件\n")
    display = summary[
        ["company_name", "event_date", "independence", "beta", "r_squared",
         "car_day0_pct", "car_day5_pct", "car_day20_pct", "trough_day", "trough_car_pct",
         "latest_day", "noise_band_1sd_pct"]
    ].copy()
    for column in display.columns:
        if column.endswith("_pct") or column in {"beta", "r_squared"}:
            display[column] = display[column].round(2)
    print(display.to_string(index=False))

    weak = summary[summary["r_squared"] < 0.2]
    if not weak.empty:
        print(
            f"\n警告: R²が0.2未満のイベントが{len(weak)}件あります"
            f"（{', '.join(weak['company_name'])}）。"
            "\n  市場モデルの説明力が弱く、異常リターンの大半はイベント以外の個別要因を含みます。"
        )
    print(
        "\nnoise_band_1sd_pct は、推定窓の残差標準偏差×√経過日数。"
        "\n  CARがこの幅に収まっていれば、平常時の変動と区別できません（検定ではなく桁感）。"
    )
    print(f"\n結果はローカル専用ディレクトリへ保存しました: {output_dir}")
    print("\n**n=6。統計的な一般化はできない。各件の事情はindependence列を参照。**")
    return panel, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=INPUT_DIR / "breach_events.csv")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--benchmark", default="1348.T", help="既定はMAXIS TOPIX。1306.Tは分割の調整漏れがあり使えない")
    args = parser.parse_args()
    run_analysis(events_path=args.events, output_dir=args.out, benchmark=args.benchmark)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
