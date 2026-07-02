"""공용 예외 정의

모듈별 커스텀 예외는 AppError 상속으로 정의
FastAPI 예외 핸들러는 main.py 조립 시점에 등록 예정
"""


class AppError(Exception):
    """서비스 공통 베이스 예외"""

    code: str = "APP_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(AppError):
    code = "NOT_FOUND"


class ExternalServiceError(AppError):
    """LLM·MCP·임베딩 등 외부 호출 실패 (비기능: 안정성)"""

    code = "EXTERNAL_SERVICE_ERROR"
