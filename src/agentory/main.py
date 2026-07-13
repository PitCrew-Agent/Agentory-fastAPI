"""Agentory FastAPI 앱 엔트리포인트 (DEV_SERVER)"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentory.common.context import get_locale
from agentory.common.exceptions import AppError
from agentory.common.i18n import translate
from agentory.common.middleware import ContextMiddleware
from agentory.core.config import get_settings
from agentory.core.logging import setup_logging
from agentory.modules.admin.router import router as admin_router
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


# 태그별 그룹 라벨, Swagger 엔드포인트 그룹 헤더에 표시
OPENAPI_TAGS = [
    {"name": "auth", "description": "로그인·로그아웃·토큰 갱신·현재 사용자"},
    {
        "name": "admin",
        "description": "담당 라인 CRUD·유저 담당 라인·설비 책임자 지정 (관리자 전용)",
    },
    {"name": "chat", "description": "Tory 질의응답·대화 히스토리"},
    {"name": "notifications", "description": "알림 목록(커서 페이지네이션)·읽음·실시간 스트림"},
    {"name": "telemetry", "description": "설비 상태·상세·센서 시계열·라인"},
    {"name": "work-logs", "description": "작업 로그 CRUD (수정·삭제는 작성자만)"},
    {"name": "system", "description": "헬스 체크"},
]


def _register_exception_handlers(app: FastAPI) -> None:
    # 도메인 예외·검증 오류를 통일 에러 포맷({code, message})으로 직렬화 (INFRA_AOP01)
    # 메시지는 요청 로케일(Accept-Language)로 번역, 라우터별 try/except 대체
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        message = translate(exc.message_code, get_locale(), **exc.params)
        return JSONResponse(
            status_code=exc.http_status, content={"code": exc.code, "message": message}
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 검증 오류도 통일 포맷, 상세(detail)는 디버깅용으로 함께 노출
        message = translate("error.validation", get_locale())
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": message, "detail": exc.errors()},
        )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Agentory API",
        description="자율형 제조 데이터 분석 및 MCP 에이전트 시스템 백엔드 API",
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    _register_exception_handlers(app)

    app.middleware("http")(oidc_auth_middleware)
    # 컨텍스트(request_id·locale)는 인증보다 먼저 설정되도록 인증 다음에 등록(더 바깥)
    app.add_middleware(ContextMiddleware)
    # CORS는 최외곽에 두어 preflight가 인증 미들웨어보다 먼저 처리되도록 마지막에 등록
    # HttpOnly 쿠키 인증을 위해 credentials 허용
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        # fetch 기반 SSE가 보내는 Cache-Control·Last-Event-ID 등 비안전 헤더 preflight 허용
        # credentials 동반 시 Starlette가 요청 헤더를 그대로 echo하므로 와일드카드 안전
        allow_headers=["*"],
        # 스트림 재연결 시 프론트가 마지막 이벤트 ID 읽도록 노출
        expose_headers=["Last-Event-ID"],
    )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
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
