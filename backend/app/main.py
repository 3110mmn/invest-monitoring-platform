"""
FastAPI エントリーポイント
"""
import re
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.analytics_connection import (
    close_analytics_connection,
    get_analytics_connection,
    open_analytics_connection,
)
from app.config import settings
from app.database import Connection, get_readiness_db
from app.errors import error_body, register_error_handlers
from app.routers.domain import financials, investment_targets, mandates, relationships, securities, themes
from app.security import SAFE_METHODS, management_key_from_request, verify_management_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        open_analytics_connection()
        yield
    finally:
        close_analytics_connection()


app = FastAPI(
    title="投資判断プラットフォーム API",
    description="投資テーマ管理・指標モニタリング・トリガー判定・ポートフォリオ管理",
    version="1.0.0",
    lifespan=lifespan,
)

register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PUBLIC_DEMO_WRITE_PATHS = (
    ("POST", re.compile(r"^/api/mandates/$")),
    ("PATCH", re.compile(r"^/api/mandates/\d+$")),
    ("DELETE", re.compile(r"^/api/mandates/\d+$")),
    ("PUT", re.compile(r"^/api/mandates/\d+/investment-targets/\d+$")),
    ("DELETE", re.compile(r"^/api/mandates/\d+/investment-targets/\d+$")),
)
_public_demo_rate_windows: dict[str, tuple[int, int]] = {}


def _public_demo_write_allowed(method: str, path: str) -> bool:
    return any(
        allowed_method == method and pattern.fullmatch(path)
        for allowed_method, pattern in _PUBLIC_DEMO_WRITE_PATHS
    )


def _public_demo_rate_limit_exceeded(client_key: str) -> bool:
    """Best-effort per-process throttle; DB quotas are the durable safeguard."""
    now = int(time.time())
    window_start = now - (now % 60)
    if len(_public_demo_rate_windows) > 2048:
        for key, (key_window, _) in list(_public_demo_rate_windows.items()):
            if key_window != window_start:
                _public_demo_rate_windows.pop(key, None)
    prior_window, count = _public_demo_rate_windows.get(client_key, (window_start, 0))
    if prior_window != window_start:
        prior_window, count = window_start, 0
    if count >= settings.public_demo_writes_per_minute:
        return True
    _public_demo_rate_windows[client_key] = (prior_window, count + 1)
    return False


@app.middleware("http")
async def reject_writes_in_read_only_mode(request: Request, call_next):
    """変更操作をread-only guardと管理キー認証の二段階で保護する。"""
    if settings.public_demo_write_enabled and (
        request.url.path == "/api/data" or request.url.path.startswith("/api/data/")
    ):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=error_body(status.HTTP_404_NOT_FOUND, "Not found"),
        )
    if request.method in SAFE_METHODS:
        return await call_next(request)
    if settings.database_read_only:
        return JSONResponse(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            content=error_body(
                status.HTTP_405_METHOD_NOT_ALLOWED, "API is running in read-only mode"
            ),
        )
    if settings.public_demo_write_enabled:
        if (
            settings.management_api_enabled
            or settings.database_environment != "demo"
            or settings.database_username != "invest_demo_writer"
        ):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=error_body(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Public demo writes require the dedicated demo database role and disabled management API",
                ),
            )
        if not _public_demo_write_allowed(request.method, request.url.path):
            return JSONResponse(
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                content=error_body(
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                    "This operation is not available in the public demo",
                ),
            )
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > 32_768:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content=error_body(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "Public demo request body exceeds 32 KB",
                ),
            )
        client_key = request.client.host if request.client else "unknown"
        if _public_demo_rate_limit_exceeded(client_key):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=error_body(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many public demo writes; try again later",
                ),
            )
        return await call_next(request)
    if settings.management_api_enabled:
        try:
            verify_management_key(management_key_from_request(request))
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=error_body(exc.status_code, str(exc.detail)),
            )
    return await call_next(request)

app.include_router(themes.router, prefix="/api")
app.include_router(mandates.router, prefix="/api")
app.include_router(investment_targets.router, prefix="/api")
app.include_router(financials.router, prefix="/api")
app.include_router(relationships.router, prefix="/api")
app.include_router(securities.router, prefix="/api")
if not settings.database_read_only and not settings.public_demo_write_enabled:
    # 公開コンテナには管理処理の依存モジュールも管理Routeも読み込ませない。
    from app.routers.pipeline import data

    app.include_router(data.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness(db: Connection = Depends(get_readiness_db)):
    try:
        db.execute("SELECT 1").fetchone()
        rows = db.execute("""
            WITH ranked AS (
                SELECT job_type, status, finished_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY job_type
                           ORDER BY started_at DESC, ingestion_run_id DESC
                       ) AS row_number
                FROM ingestion_run
            )
            SELECT job_type, status, finished_at
            FROM ranked
            WHERE row_number = 1
            ORDER BY job_type
        """).fetchall()
    except psycopg.Error:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unavailable",
                "database_environment": settings.database_environment,
            },
        )

    latest_ingestions = [dict(row) for row in rows]
    degraded = any(
        ingestion["status"] in {"failed", "partial"}
        for ingestion in latest_ingestions
    )
    # 価格は分析層からしか読まないので、ここが落ちていると価格が一切出ない。
    # PostgreSQLが健全でも ready とは言えない。
    analytics = "ok" if get_analytics_connection() is not None else "unavailable"
    if analytics != "ok":
        degraded = True
    return {
        "status": "degraded" if degraded else "ready",
        "database": "ok",
        "analytics": analytics,
        # 接続先の分類。接続文字列は返さない。ローカルで開発DBと実データDBの
        # 区別が画面から付かないと、実データを開発だと思って編集する事故が起きる。
        "database_environment": settings.database_environment,
        "latest_ingestions": latest_ingestions,
    }
