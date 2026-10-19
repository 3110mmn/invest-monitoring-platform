"""CSVを時点付きTheme Membershipとして取り込む。

例:
  python -m scripts.import_theme_members \
    --theme-key retail-edinet-pilot --theme-name "EDINET DB 小売Pilot" \
    --csv ../取り込みデータ/Master/ThemeMembership/retail-edinet-pilot.csv \
    --industry 小売業 --mode replace --snapshot-at 2026-10-19 --dry-run

CSVの会社名は識別・表示に使わず、jpx_codeを全銘柄Parquetへ照合して正規名称を採用する。
投資対象は必要な銘柄だけPostgreSQLへ遅延登録し、Watchlistには追加しない。
appendはCSVにある銘柄だけを追加する。replaceはCSVを指定時点の正本として、
CSVから消えた現在Membershipのeffective_toも閉じる。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.runner import connect_lake
from app.config import settings
from app.database import connect_database
from app.etl.normalizers import PRODUCT_TYPES
from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.security_source import ParquetSecuritySource
from app.repositories.theme_membership_import_repository import (
    ThemeMembershipImportRepository,
)
from app.repositories.theme_repository import ThemeRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = (
    PROJECT_ROOT
    / "取り込みデータ"
    / "Master"
    / "ThemeMembership"
    / "retail-edinet-pilot.csv"
)


def read_jpx_codes(path: Path, industry: str | None) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"jpx_code", "industry"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSVに必須列がありません: {', '.join(sorted(missing))}")
        codes = {
            str(row["jpx_code"]).strip()
            for row in reader
            if str(row.get("jpx_code") or "").strip()
            and (industry is None or str(row.get("industry") or "").strip() == industry)
        }
    return sorted(codes)


def target_data(security: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_key": security["target_key"],
        "target_name": security["company_name"],
        "target_type": PRODUCT_TYPES.get(security["product_category_code"] or ""),
        "market": security["market_name"],
        "currency": "JPY",
    }


def parse_snapshot_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "snapshot-atはYYYY-MM-DDまたはISO 8601日時で指定してください"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def artifact_ref(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_membership_diff(
    current_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
    mode: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str], list[str]]:
    """現在MembershipとCSVを比較し、追加・除外・継続のtarget_keyを返す。"""
    current_by_key = {row["target_key"]: row for row in current_rows}
    incoming_by_key = {row["target_key"]: row for row in incoming_rows}
    added_keys = sorted(incoming_by_key.keys() - current_by_key.keys())
    removed_keys = (
        sorted(current_by_key.keys() - incoming_by_key.keys())
        if mode == "replace"
        else []
    )
    unchanged_keys = sorted(incoming_by_key.keys() & current_by_key.keys())
    return current_by_key, incoming_by_key, added_keys, removed_keys, unchanged_keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme-key", required=True)
    parser.add_argument("--theme-name", required=True)
    parser.add_argument("--description")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--industry")
    parser.add_argument("--mode", choices=("append", "replace"), default="append")
    parser.add_argument("--snapshot-at", type=parse_snapshot_at)
    parser.add_argument("--rationale")
    parser.add_argument("--actor", default="owner")
    parser.add_argument("--lake", default=settings.parquet_lake or str(PROJECT_ROOT / "data" / "parquet"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.csv.is_file():
        parser.error(
            f"CSVが見つかりません: {args.csv}。"
            "取り込みデータ/README.mdの配置契約を確認してください"
        )
    if args.mode == "replace" and args.snapshot_at is None:
        parser.error("replaceモードでは--snapshot-atを明示してください")
    snapshot_at = args.snapshot_at or datetime.now(UTC)

    codes = read_jpx_codes(args.csv, args.industry)
    if not codes:
        parser.error("条件に一致するjpx_codeがCSVにありません")

    analytics = connect_lake(args.lake)
    source = ParquetSecuritySource(analytics)
    resolved = []
    missing = []
    for code in codes:
        security = source.find_by_jpx_code(code)
        if security is None or not security["target_key"]:
            missing.append(code)
        else:
            resolved.append(security)
    analytics.close()

    result: dict[str, Any] = {
        "theme_key": args.theme_key,
        "snapshot_at": snapshot_at.isoformat(),
        "import_mode": args.mode,
        "artifact_name": args.csv.name,
        "artifact_ref": artifact_ref(args.csv),
        "artifact_hash": sha256_file(args.csv),
        "csv_rows": len(codes),
        "resolved": len(resolved),
        "missing_jpx_codes": missing,
        "dry_run": args.dry_run,
    }
    if missing:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    conn = connect_database(read_only=False)
    try:
        themes = ThemeRepository(conn)
        theme = themes.find_by_key(args.theme_key)
        if theme is not None and not theme["is_active"]:
            raise RuntimeError("アーカイブ済みThemeには取り込めません。先に有効へ戻してください")
        theme_id = int(theme["theme_id"]) if theme is not None else None
        targets = InvestmentTargetRepository(conn)
        current_rows = targets.get_theme_investment_targets(theme_id) if theme_id else []
        (
            current_by_key,
            incoming_by_key,
            added_keys,
            removed_keys,
            unchanged_keys,
        ) = calculate_membership_diff(
            current_rows,
            resolved,
            args.mode,
        )
        for key in removed_keys:
            if current_by_key[key]["effective_from"] > snapshot_at:
                raise ValueError(
                    f"snapshot_atより後に開始したMembershipは終了できません: {key}"
                )
        result.update({
            "members_added": len(added_keys),
            "members_removed": len(removed_keys),
            "members_unchanged": len(unchanged_keys),
            "added_target_keys": added_keys,
            "removed_target_keys": removed_keys,
        })
        if args.dry_run:
            result["theme_exists"] = theme is not None
            result["members"] = [
                {
                    "jpx_code": row["jpx_code"],
                    "target_key": row["target_key"],
                    "name": row["company_name"],
                }
                for row in resolved
            ]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        description = args.description or (
            f"CSV import: {args.csv.name}"
            + (f" / industry={args.industry}" if args.industry else "")
        )
        # 以降は必ず既存または新規のThemeが確定する。`theme_id`は上で
        # Optionalとして始まるため、ここで非Optionalへ畳んでから使う。
        if theme is None:
            resolved_theme_id = themes.create({
                "theme_key": args.theme_key,
                "theme_name": args.theme_name,
                "description": description,
            })
            result["theme_created"] = True
        else:
            resolved_theme_id = int(theme["theme_id"])
            themes.update(resolved_theme_id, {
                "theme_name": args.theme_name,
                "description": description,
            })
            result["theme_created"] = False
        theme_id = resolved_theme_id

        imports = ThemeMembershipImportRepository(conn)
        duplicate = imports.find_duplicate(
            theme_id=theme_id,
            snapshot_at=snapshot_at,
            import_mode=args.mode,
            artifact_hash=result["artifact_hash"],
        )
        if duplicate is not None:
            result.update({
                "theme_id": theme_id,
                "theme_created": False,
                "import_id": duplicate["import_id"],
                "duplicate_skipped": True,
            })
            conn.rollback()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        target_ids: dict[str, int] = {}
        for key in added_keys:
            target_ids[key] = targets.ensure_from_security(target_data(incoming_by_key[key]))
        import_id = imports.create({
            "theme_id": theme_id,
            "snapshot_at": snapshot_at,
            "import_mode": args.mode,
            "artifact_name": result["artifact_name"],
            "artifact_ref": result["artifact_ref"],
            "artifact_hash": result["artifact_hash"],
            "rationale": args.rationale,
            "actor": args.actor,
            "input_count": len(resolved),
            "added_count": len(added_keys),
            "removed_count": len(removed_keys),
            "unchanged_count": len(unchanged_keys),
        })
        for key in added_keys:
            targets.add_theme_investment_target(
                theme_id,
                target_ids[key],
                effective_from=snapshot_at,
                import_id=import_id,
                reason=args.rationale,
            )
        for key in removed_keys:
            targets.deactivate_theme_investment_target(
                theme_id,
                int(current_by_key[key]["target_id"]),
                effective_to=snapshot_at,
                import_id=import_id,
                reason=args.rationale,
            )
        conn.commit()
        result.update({
            "theme_id": theme_id,
            "import_id": import_id,
            "duplicate_skipped": False,
            "members_total": len(current_by_key) + len(added_keys) - len(removed_keys),
        })
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
