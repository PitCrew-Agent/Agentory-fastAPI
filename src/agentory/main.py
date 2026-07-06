"""Agentory FastAPI ???뷀듃由ы룷?명듃 (DEV_SERVER)"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from agentory.core.config import get_settings
from agentory.core.logging import setup_logging
from agentory.modules.auth.middleware import audit_logging_middleware
from agentory.modules.auth.router import router as auth_router
from agentory.modules.chat.router import router as chat_router
from agentory.modules.telemetry.router import router as telemetry_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # TODO(二쇳씗??: watcher 諛깃렇?쇱슫?????쒖옉 (NEW_PROACT01_DETECT01)
    yield
    # TODO(二쇳씗??: watcher 醫낅즺 泥섎━


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Agentory API",
        description="Manufacturing data analysis and MCP agent system",
        version="0.1.0",
        lifespan=lifespan,
    )

    # TODO(?덈???: OIDC/JWT 寃利?誘몃뱾?⑥뼱 ?깅줉 (BE_AUTH01_OAUTH01)
    # app.middleware("http")(...)

    app.middleware("http")(audit_logging_middleware)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(telemetry_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()


def run() -> None:
    """Run the local API server."""
    uvicorn.run(
        "agentory.main:app",
        host="0.0.0.0",
        port=8000,
        reload=get_settings().app_env == "local",
    )


