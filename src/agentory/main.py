"""Agentory FastAPI 앱 엔트리포인트 (DEV_SERVER)"""

import asyncio
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentory.common.context import get_locale
from agentory.common.exceptions import AppError
from agentory.common.i18n import translate
from agentory.common.middleware import ContextMiddleware
from agentory.common.response import ApiResponse
from agentory.core.config import get_settings
from agentory.core.logging import setup_logging
from agentory.modules.admin.router import router as admin_router
from agentory.modules.auth.middleware import oidc_auth_middleware
from agentory.modules.auth.router import router as auth_router
from agentory.modules.chat.router import router as chat_router
from agentory.modules.incident.router import router as incident_router
from agentory.modules.notification.router import router as notification_router
from agentory.modules.telemetry.router import router as telemetry_router
from agentory.modules.watcher.sync_worker import run_sync_loop
from agentory.modules.worklog.router import router as worklog_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 알림 동기화 워처 시작, 클라이언트 접속과 무관하게 알람을 알림화 (NEW_PROACT01_DETECT01)
    interval = get_settings().notification_sync_interval_seconds
    watcher_task = asyncio.create_task(run_sync_loop(interval))
    yield
    # 앱 종료 시 워처 취소 후 정리 완료까지 대기
    watcher_task.cancel()
    with suppress(asyncio.CancelledError):
        await watcher_task


# 태그별 그룹 라벨, Swagger 엔드포인트 그룹 헤더에 표시
OPENAPI_TAGS = [
    {"name": "auth", "description": "로그인·로그아웃·토큰 갱신·현재 사용자"},
    {
        "name": "admin",
        "description": "담당 라인 CRUD·유저 담당 라인·설비 책임자 지정 (관리자 전용)",
    },
    {"name": "chat", "description": "Tory 질의응답·대화 히스토리"},
    {"name": "notifications", "description": "알림 목록(커서 페이지네이션)·읽음·실시간 스트림"},
    {"name": "incident-plans", "description": "알림 기반 대응 계획·작업 로그 초안 생성"},
    {"name": "telemetry", "description": "설비 상태·상세·센서 시계열·라인"},
    {"name": "work-logs", "description": "작업 로그 CRUD (수정·삭제는 작성자만)"},
    {"name": "system", "description": "헬스 체크"},
]


# HTTP 상태코드 -> 에러 code 파생 (ApiResponse code 필드용), 미매핑은 HTTP_{status}
_STATUS_CODE_NAMES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
}


def _fail(status_code: int, code: str, message: str, result: object = None) -> JSONResponse:
    # 실패 ApiResponse 응답 {success:false, code, message, result} (INFRA_AOP01)
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "code": code, "message": message, "result": result},
    )


def _register_exception_handlers(app: FastAPI) -> None:
    # 도메인 예외·검증 오류·잔여 HTTPException을 ApiResponse 실패 응답으로 통일 (INFRA_AOP01)
    # 메시지는 요청 로케일(Accept-Language)로 번역, 라우터별 try/except 대체
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        message = translate(exc.message_code, get_locale(), **exc.params)
        return _fail(exc.http_status, exc.code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 검증 오류도 통일 ApiResponse, 필드별 상세는 result에 실어 프론트가 활용
        message = translate("error.validation", get_locale())
        return _fail(422, "VALIDATION_ERROR", message, jsonable_encoder(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 잔여 HTTPException(auth 프로토콜 오류·미매칭 경로 등)도 ApiResponse로 통일
        code = _STATUS_CODE_NAMES.get(exc.status_code, f"HTTP_{exc.status_code}")
        return _fail(exc.status_code, code, str(exc.detail))


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
    app.include_router(incident_router, prefix="/api/v1")
    app.include_router(worklog_router, prefix="/api/v1")

    @app.get("/health", tags=["system"], response_model=ApiResponse[dict])
    async def health() -> ApiResponse[dict]:
        return ApiResponse.ok({"status": "ok", "env": settings.app_env})

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
