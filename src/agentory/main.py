"""Agentory FastAPI 앱 엔트리포인트 (DEV_SERVER)"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from agentory.core.config import get_settings
from agentory.core.logging import setup_logging
from agentory.modules.auth.middleware import oidc_auth_middleware
from agentory.modules.auth.router import router as auth_router
from agentory.modules.chat.router import router as chat_router
from agentory.modules.notification.router import router as notification_router
from agentory.modules.telemetry.router import router as telemetry_router
from agentory.modules.worklog.router import router as worklog_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # TODO(주희정): watcher 백그라운드 잡 시작 (NEW_PROACT01_DETECT01)
    yield
    # TODO(주희정): watcher 종료 처리


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Agentory API",
        description="자율형 제조 데이터 분석 및 MCP 에이전트 시스템",
        version="0.1.0",
        lifespan=lifespan,
    )

    # TODO(안민호): OIDC/JWT 검증 미들웨어 등록 (BE_AUTH01_OAUTH01)
    # app.middleware("http")(...)

    app.middleware("http")(oidc_auth_middleware)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(telemetry_router, prefix="/api/v1")
    app.include_router(notification_router, prefix="/api/v1")
    app.include_router(worklog_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()


def run() -> None:
    """uv run agentory-api 로컬 실행용"""
    uvicorn.run(
        "agentory.main:app",
        host="0.0.0.0",
        port=8000,
        reload=get_settings().app_env == "local",
    )
