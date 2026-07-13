"""공용 예외 정의 (INFRA_AOP01)

도메인 코드는 리터럴 HTTPException 대신 이 예외를 message_code로 raise
전역 예외 핸들러(main.py)가 code·http_status·번역 메시지로 통일 직렬화
message_code는 agentory.common.i18n.MESSAGES 키, params는 보간값
"""


class AppError(Exception):
    """서비스 공통 베이스 예외, 전역 핸들러가 code·http_status·메시지로 변환"""

    code: str = "APP_ERROR"
    http_status: int = 500

    def __init__(
        self,
        message_code: str,
        *,
        params: dict[str, object] | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ):
        self.message_code = message_code  # i18n 카탈로그 키(미등록 시 문자열 폴백)
        self.params = params or {}
        if code:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(message_code)


class NotFoundError(AppError):
    """대상 리소스 없음 (404)"""

    code = "NOT_FOUND"
    http_status = 404


class PermissionDeniedError(AppError):
    """권한 없음 (403)"""

    code = "FORBIDDEN"
    http_status = 403


class ValidationError(AppError):
    """요청 값·상태 위반 (400)"""

    code = "VALIDATION_ERROR"
    http_status = 400


class ConflictError(AppError):
    """유일 제약 등 충돌 (409)"""

    code = "CONFLICT"
    http_status = 409


class ExternalServiceError(AppError):
    """LLM·MCP·임베딩 등 외부 호출 실패 (비기능: 안정성)"""

    code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502
