"""
FastAPI エントリーポイント
"""
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
from app.routers.domain import financials, investment_targets, relationships, themes
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


@app.middleware("http")
async def reject_writes_in_read_only_mode(request: Request, call_next):
    """変更操作をread-only guardと管理キー認証の二段階で保護する。"""
    if request.method in SAFE_METHODS:
        return await call_next(request)
    if settings.database_read_only:
        return JSONResponse(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            content=error_body(
                status.HTTP_405_METHOD_NOT_ALLOWED, "API is running in read-only mode"
            ),
        )
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
app.include_router(investment_targets.router, prefix="/api")
app.include_router(financials.router, prefix="/api")
app.include_router(relationships.router, prefix="/api")
if not settings.database_read_only:
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
