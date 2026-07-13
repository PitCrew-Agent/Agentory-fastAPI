"""요청 스코프 컨텍스트 contextvar (INFRA_AOP01)

미들웨어가 요청 시작 시 locale·request_id를 설정, 서비스·예외 핸들러가 인자 전달 없이 참조
요청마다 독립 컨텍스트라 값이 다른 요청으로 새지 않음, 미설정 시 기본값 사용
"""

from contextvars import ContextVar

DEFAULT_LOCALE = "ko"

_locale: ContextVar[str] = ContextVar("locale", default=DEFAULT_LOCALE)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_locale(locale: str) -> None:
    _locale.set(locale)


def get_locale() -> str:
    return _locale.get()


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()
