"""Replay a supported trigger definition without writing operational state.

Run from backend, for example:
python -m scripts.replay_trigger --lake ../data/parquet --target 2796.T \
  --metric operating_income --comparison forecast_variance --operator lte --threshold -10
"""

import argparse
import json
from datetime import date
from decimal import Decimal

from analytics.runner import FINANCIAL_SUBPATH, lake_glob, open_connection, sql_literal
from app.services.trigger import evaluate, read_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", required=True)
    parser.add_argument("--target", required=True, help="target_key, e.g. 2796.T")
    parser.add_argument("--metric", choices=("revenue", "operating_income", "net_income"), required=True)
    parser.add_argument(
        "--comparison",
        choices=("forecast_variance", "yoy", "absolute", "sign_transition"),
        required=True,
    )
    parser.add_argument("--operator", choices=("lte", "gte", "crosses_below", "crosses_above"), required=True)
    parser.add_argument("--threshold", type=Decimal)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.comparison == "sign_transition":
        if not args.operator.startswith("crosses_") or args.threshold is not None:
            parser.error("sign_transition requires a crosses operator and no threshold")
    elif args.operator.startswith("crosses_") or args.threshold is None:
        parser.error("numeric comparisons require lte/gte and --threshold")
    glob = lake_glob(args.lake, FINANCIAL_SUBPATH)
    with open_connection(glob) as connection:
        connection.execute(
            "CREATE VIEW financial_disclosure AS SELECT * FROM read_parquet("
            + sql_literal(glob)
            + ", hive_partitioning=true)"
        )
        rows = read_inputs(connection, args.target, args.metric, args.comparison)
    results = [
        evaluate(row, args.operator, args.threshold, args.as_of) for row in rows if row["available_on"] <= args.as_of
    ]
    print(
        json.dumps(
            {
                "mode": "replay",
                "target": args.target,
                "metric": args.metric,
                "comparison": args.comparison,
                "as_of": str(args.as_of),
                "results": results,
            },
            default=str,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
