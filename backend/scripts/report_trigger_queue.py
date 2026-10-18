"""Print the current operational Trigger queue summary for scheduler logs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect_database
from app.repositories.trigger_repository import TriggerRepository


def main() -> int:
    connection = connect_database(read_only=True)
    try:
        repo = TriggerRepository(connection)
        actionable = repo.evaluations(work_state="actionable", mode="live")
        unhandled = repo.evaluations(work_state="unhandled", mode="live")
    finally:
        connection.close()
    print(
        json.dumps(
            {"actionable": len(actionable), "unhandled": len(unhandled)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
