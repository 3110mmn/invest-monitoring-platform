from pathlib import Path
from typing import Any

from app.database import Connection
from app.etl.fetchers.jquants import JQuantsClient
from app.etl.lineage import display_path, finish_run, stage_raw, start_run
from app.etl.loaders import (
    ensure_data_source,
    record_ingestion_errors,
    upsert_investment_target_master,
)
from app.etl.models import ValidationIssue
from app.etl.normalizers import (
    normalize_jquants_master,
)


def _write_raw(raw_dir: Path, job_type: str, run_id: int, records: list[dict[str, Any]]) -> Path:
    """J-Quantsのrawを保存する。保存先の規則は lineage が持つ。"""
    return stage_raw(raw_dir, "jquants", job_type, run_id, records)


def _display_path(path: Path, project_root: Path) -> str:
    return display_path(path, project_root)


def _start_run(conn: Connection, source_id: int, job_type: str, project_root: Path,
               date_from: str | None = None, date_to: str | None = None, target_count: int = 0) -> int:
    return start_run(conn, source_id, job_type, project_root,
                     date_from=date_from, date_to=date_to, target_count=target_count)


def _finish_run(conn: Connection, run_id: int, *, status: str, fetched: int,
                loaded: int, skipped: int, failed: int, raw_path: str | None,
                error_message: str | None = None) -> None:
    finish_run(conn, run_id, status=status, fetched=fetched, loaded=loaded,
               skipped=skipped, failed=failed, raw_path=raw_path, error_message=error_message)


class JQuantsMarketPipeline:
    def __init__(self, conn: Connection, client: JQuantsClient, raw_dir: Path, project_root: Path) -> None:
        self.conn = conn
        self.client = client
        self.raw_dir = raw_dir
        self.project_root = project_root
        self.source_id = ensure_data_source(conn, "jquants", "J-Quants", client.base_url)

    def sync_master(self, *, code: str | None = None, date: str | None = None) -> dict[str, int]:
        job_type = "jquants_equities_master_v1"
        run_id = _start_run(self.conn, self.source_id, job_type, self.project_root, target_count=1 if code else 0)
        try:
            raw = self.client.equities_master(code=code, date=date)
            path = _write_raw(self.raw_dir, job_type, run_id, raw)
            records = [normalize_jquants_master(row) for row in raw]
            loaded = upsert_investment_target_master(self.conn, self.source_id, records)
            _finish_run(self.conn, run_id, status="succeeded", fetched=len(raw), loaded=loaded,
                        skipped=0, failed=0, raw_path=_display_path(path, self.project_root))
            self.conn.commit()
            return {"run_id": run_id, "fetched": len(raw), "loaded": loaded, "failed": 0}
        except Exception as exc:
            _finish_run(self.conn, run_id, status="failed", fetched=0, loaded=0, skipped=0,
                        failed=1, raw_path=None, error_message=str(exc))
            self.conn.commit()
            raise

    def archive_market_prices(self, dates: list[str]) -> dict[str, int]:
        """指定した営業日の**全銘柄**四本値をrawとして保存する。PostgreSQLへはロードしない。

        PostgreSQLはControl Plane（何を監視するか）であり、全市場の履歴はData Plane側の
        責務になる。ここで正規化やUPSERTを行うと、配信に不要なデータをServing DBへ
        溜め込むことになるため、rawを残すところで止める。分析はParquet / DWHが担う。

        取得単位を日付にすると、リクエスト数が銘柄数ではなく日数にしか比例しない。

        **1日 = 1 ingestion_run = 1 rawファイル**とする。2年分をまとめて1ファイルにすると
        メモリに200万件以上を抱え、日次実行との保存レイアウトも食い違う。日付で分けておけば
        Parquetの分割単位ともそのまま一致し、特定の日だけ取り直すこともできる。
        """
        job_type = "jquants_market_prices_archive_v1"
        archived = failed = records = 0
        for day in dates:
            run_id = _start_run(
                self.conn, self.source_id, job_type, self.project_root, day, day, 1
            )
            try:
                rows = self.client.daily_prices(date=day)
            except Exception as exc:
                # 1日失敗しても残りを続ける。プランの窓の外や休場日が混ざりうる。
                record_ingestion_errors(
                    self.conn, run_id, "fetch",
                    [ValidationIssue(day, "fetch_error", str(exc), True)],
                )
                _finish_run(self.conn, run_id, status="failed", fetched=0, loaded=0,
                            skipped=0, failed=1, raw_path=None, error_message=str(exc))
                self.conn.commit()
                failed += 1
                continue

            raw_path = None
            if rows:
                raw_path = _display_path(
                    _write_raw(self.raw_dir, job_type, run_id, rows), self.project_root
                )
            # 休場日はAPIが空で返す。失敗ではないのでsucceededとして残す。
            _finish_run(self.conn, run_id, status="succeeded", fetched=len(rows), loaded=0,
                        skipped=0, failed=0, raw_path=raw_path)
            self.conn.commit()
            archived += 1
            records += len(rows)

        return {"dates": archived, "fetched": records, "failed": failed}

    def archive_financials(self, dates: list[str]) -> dict[str, int]:
        """指定した開示日の**全銘柄**の財務サマリーをrawとして保存する。

        価格の `archive_market_prices` と同じ方針で、PostgreSQLへはロードしない。
        全市場の履歴はData Plane側の責務であり、Serving DBへ溜め込まない。

        1日 = 1 ingestion_run = 1 rawファイル。開示が無い日はAPIが空で返すが、
        取得できなかったわけではないので失敗にしない。
        """
        job_type = "jquants_financial_archive_v1"
        archived = failed = records = 0
        for day in dates:
            run_id = _start_run(
                self.conn, self.source_id, job_type, self.project_root, day, day, 1
            )
            try:
                rows = self.client.financial_summaries(date=day)
            except Exception as exc:
                record_ingestion_errors(
                    self.conn, run_id, "fetch",
                    [ValidationIssue(day, "fetch_error", str(exc), True)],
                )
                _finish_run(self.conn, run_id, status="failed", fetched=0, loaded=0,
                            skipped=0, failed=1, raw_path=None, error_message=str(exc))
                self.conn.commit()
                failed += 1
                continue

            raw_path = None
            if rows:
                raw_path = _display_path(
                    _write_raw(self.raw_dir, job_type, run_id, rows), self.project_root
                )
            _finish_run(self.conn, run_id, status="succeeded", fetched=len(rows), loaded=0,
                        skipped=0, failed=0, raw_path=raw_path)
            self.conn.commit()
            archived += 1
            records += len(rows)

        return {"dates": archived, "fetched": records, "failed": failed}
