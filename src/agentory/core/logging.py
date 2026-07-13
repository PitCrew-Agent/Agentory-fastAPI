"""로깅 공통 설정 (INFRA04_LOGGING01 / INFRA_AOP01)

각 모듈은 logging.getLogger(__name__)으로 로거 사용, 여기서는 포맷·레벨 공통 설정만 제공
요청 컨텍스트의 request_id를 모든 로그 레코드에 주입해 한 요청의 로그를 묶어 추적
TODO(안민호): 로그 취합·오류 추적 체계 구성
"""

import logging

from agentory.common.context import get_request_id
from agentory.core.config import get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s | %(message)s"


class RequestIdFilter(logging.Filter):
    # 현재 요청 컨텍스트의 request_id를 레코드에 주입, 컨텍스트 밖이면 '-'
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def setup_logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format=LOG_FORMAT)
    # 모든 핸들러에 request_id 주입 필터 부착 (중복 부착 방지)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())
    # 서드파티 라이브러리 로그 소음 억제
    logging.getLogger("httpx").setLevel(logging.WARNING)
