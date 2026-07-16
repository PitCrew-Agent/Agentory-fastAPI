"""알림 동기화 백그라운드 워처 (NEW_PROACT01_DETECT01)

클라이언트 조회·SSE 폴링과 무관하게 주기적으로 확정 알람을 알림 테이블로 동기화
프론트 연결이 끊겨도 알림이 제때 생성되도록 sync-on-read/poll 의존을 제거
개별 주기 실패는 격리해 루프 자체는 앱 생명주기 동안 계속 유지
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from agentory.core.db import SessionLocal
from agentory.modules.notification import repository

log = logging.getLogger("watcher")

# 동기화 조회 시간창, 최근 범위만 집계해 풀스캔 방지 (NEW_PROACT01_DETECT01)
# 주기 대비 충분히 커 워처 일시 중단에도 누락 없음, 신규 알람은 현재 버킷 유입이라 멱등성 유지
_SYNC_LOOKBACK = timedelta(hours=2)


async def _sync_once() -> int:
    # 한 주기 동기화, 신규 적재 건수 반환 (get_session은 자동 커밋 안 함이라 명시 commit)
    since = datetime.now(UTC) - _SYNC_LOOKBACK
    async with SessionLocal() as session:
        inserted = await repository.sync_from_alarms(session, since=since)
        await session.commit()
    return inserted


async def run_sync_loop(interval_seconds: float) -> None:
    # 취소(앱 종료)까지 주기 동기화 반복, 개별 주기 예외는 삼켜 다음 주기 재시도
    log.info("[watcher] 알림 동기화 워처 시작, 주기 %.1f초", interval_seconds)
    try:
        while True:
            try:
                inserted = await _sync_once()
                if inserted:
                    log.info("[watcher] 신규 알림 %d건 동기화", inserted)
            except Exception as exc:  # 일시적 DB 오류 등이 루프를 죽이지 않도록 격리
                log.warning("[watcher] 동기화 실패, 다음 주기 재시도: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        log.info("[watcher] 알림 동기화 워처 종료")
        raise
