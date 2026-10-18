"""Publish staged raw responses and persist their immutable GCS URIs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import connect_database
from app.etl.raw_publisher import publish_pending_raw, upload_immutable_object_with_gcs


def main() -> None:
    if not settings.raw_data_uri:
        raise RuntimeError("RAW_DATA_URI is not configured")
    project_root = Path(__file__).resolve().parents[2]
    connection = connect_database(read_only=False)
    try:
        result = publish_pending_raw(
            connection,
            raw_dir=settings.raw_data_dir,
            project_root=project_root,
            destination_base=settings.raw_data_uri,
            uploader=upload_immutable_object_with_gcs,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(f"Raw publish completed: {result}")


if __name__ == "__main__":
    main()
