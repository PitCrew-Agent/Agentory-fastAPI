"""요청 컨텍스트·액세스 로그 미들웨어 (INFRA_AOP01)

요청 시작 시 request_id·locale를 contextvar에 설정, 서비스·예외 핸들러가 참조
응답 완료 시 메서드·경로·상태·소요시간을 액세스 로그로 남기고 X-Request-ID 헤더를 부착
순수 ASGI 미들웨어라 다운스트림·예외 핸들러와 같은 컨텍스트를 공유(값 전파 보장)
"""

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentory.common.context import set_locale, set_request_id
from agentory.common.i18n import resolve_locale
from agentory.core.config import get_settings

log = logging.getLogger("access")


class ContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        # 요청 추적용 id, 클라이언트가 X-Request-ID를 주면 승계
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        set_request_id(request_id)
        set_locale(resolve_locale(headers.get("accept-language")))

        start = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # 추적용 X-Request-ID 응답 헤더 부착
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if get_settings().access_log_enabled:
                # 쿼리스트링은 토큰 노출 우려로 제외, 경로만 기록
                duration_ms = (time.perf_counter() - start) * 1000
                log.info(
                    "%s %s %d %.1fms",
                    scope.get("method", "-"),
                    scope.get("path", "-"),
                    status_code,
                    duration_ms,
                )
