"""쓰기 작업 감사 의존성 (INFRA_AOP01)

라우트에 dependencies=[Depends(audit("ACTION"))]로 붙여 비즈니스 변경을 액션명으로 감사 적재
oidc_auth_middleware의 블랭킷 HTTP_REQUEST 감사 위에 의미 있는 액션명을 더해 조회·필터를 쉽게 함
yield 의존성으로 성공·실패를 함께 기록, 사용자는 미들웨어가 request.state.user에 넣은 값 사용
"""

from collections.abc import AsyncIterator, Callable

from fastapi import Request

from agentory.modules.auth.middleware import write_audit_event


def audit(action: str) -> Callable[[Request], AsyncIterator[None]]:
    # 지정 액션명으로 감사 이벤트를 남기는 라우트 의존성 생성
    async def _dependency(request: Request) -> AsyncIterator[None]:
        user = getattr(request.state, "user", None)
        user_id = user.get("user_id") if isinstance(user, dict) else None
        try:
            yield
        except Exception as exc:
            # 도메인 예외는 http_status, HTTPException은 status_code, 그 외 500
            status_code = getattr(exc, "http_status", getattr(exc, "status_code", 500))
            await write_audit_event(
                request,
                action=action,
                status_code=status_code,
                success=False,
                user_id=user_id,
                error_message=type(exc).__name__,
            )
            raise
        await write_audit_event(
            request, action=action, status_code=200, success=True, user_id=user_id
        )

    return _dependency
