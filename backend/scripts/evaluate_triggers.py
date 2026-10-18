"""Evaluate active operational Trigger rules and persist idempotent results."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import connect_database
from app.repositories.trigger_repository import TriggerRepository
from app.services.trigger_operations import evaluate_active_rules, evaluate_rule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--rule-id", type=int)
    parser.add_argument("--orchestration-run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    connection = connect_database(read_only=False)
    repo = TriggerRepository(connection)
    try:
        if args.rule_id is None:
            summaries = evaluate_active_rules(
                repo,
                as_of=args.as_of,
                settings=settings,
                orchestration_run_id=args.orchestration_run_id,
            )
        else:
            summaries = [
                evaluate_rule(
                    repo,
                    rule_id=args.rule_id,
                    as_of=args.as_of,
                    mode="live",
                    settings=settings,
                    orchestration_run_id=args.orchestration_run_id,
                )
            ]
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps([summary.__dict__ for summary in summaries], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
