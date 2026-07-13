"""요청 컨텍스트 미들웨어 (INFRA_AOP01)

요청 시작 시 request_id·locale를 contextvar에 설정, 서비스·예외 핸들러가 참조
순수 ASGI 미들웨어라 다운스트림·예외 핸들러와 같은 컨텍스트를 공유(값 전파 보장)
액세스 로그·응답시간 측정은 Phase 3에서 이 미들웨어에 추가 예정
"""

import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from agentory.common.context import set_locale, set_request_id
from agentory.common.i18n import resolve_locale


class ContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        # 요청 추적용 id, 클라이언트가 X-Request-ID를 주면 승계
        set_request_id(headers.get("x-request-id") or uuid.uuid4().hex)
        set_locale(resolve_locale(headers.get("accept-language")))
        await self.app(scope, receive, send)
