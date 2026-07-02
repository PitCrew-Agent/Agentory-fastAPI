"""로깅 공통 설정 (INFRA04_LOGGING01 연계)

각 모듈은 logging.getLogger(__name__)으로 로거 사용, 여기서는 포맷·레벨 공통 설정만 제공
TODO(안민호): 로그 취합·오류 추적 체계 구성
"""

import logging

from agentory.core.config import get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format=LOG_FORMAT)
    # 서드파티 라이브러리 로그 소음 억제
    logging.getLogger("httpx").setLevel(logging.WARNING)
