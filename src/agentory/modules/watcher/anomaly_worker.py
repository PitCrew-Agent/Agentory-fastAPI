"""이상 감지 스코어러 백그라운드 워처 (BE_ANOM01_SERVE01)

주기(=stride)마다 설비별 최근 윈도우를 스코어링해 WRN-901 발생/해제 전이 적재
알림화는 기존 sync_from_alarms가 담당, 개별 주기 실패는 격리해 루프 유지
sync_worker와 동일한 SessionLocal + 명시 commit 패턴
"""

import asyncio
import logging
from datetime import UTC, datetime

from agentory.core.config import Settings
from agentory.core.db import SessionLocal
from agentory.modules.watcher import anomaly_repository as repo

log = logging.getLogger("anomaly-worker")


async def score_once(settings: Settings) -> int:
    """한 주기 스코어링, 신규 발령 건수 반환 (섀도우 시 관찰 저널 기록)"""
    sink = repo.resolve_sink(settings.anomaly_shadow_mode)
    async with SessionLocal() as session:
        scorer = await repo.load_scorer(session, settings)
        if not scorer.process_types:
            return 0  # 적합 모델 없음 (anomaly-fit 미실행)
        equipment = await repo.list_equipment(session)
        active = await sink.active(session)
        now = datetime.now(UTC)
        raised = 0
        for equipment_id, process_type in equipment:
            if not scorer.has(process_type):
                continue
            series = await repo.fetch_recent_series(
                session, equipment_id, settings.anomaly_lookback_rows
            )
            result = scorer.score_latest(process_type, series)
            if result is None:
                continue
            is_active = equipment_id in active
            if result.fired and not is_active:
                await sink.raise_event(session, equipment_id, result.channel, result.score, now)
                raised += 1
            elif not result.fired and is_active:
                await sink.clear(session, equipment_id, now)
        await session.commit()
        return raised


async def run_anomaly_loop(settings: Settings) -> None:
    """취소(앱 종료)까지 주기 스코어링 반복, 개별 주기 예외는 삼켜 다음 주기 재시도"""
    interval = settings.anomaly_score_interval_seconds
    log.info("[anomaly-worker] 이상 감지 워처 시작, 주기 %.1f초", interval)
    try:
        while True:
            try:
                raised = await score_once(settings)
                if raised:
                    log.info("[anomaly-worker] 신규 이상 %d건 발령", raised)
            except Exception as exc:  # 일시적 DB·모델 오류가 루프를 죽이지 않도록 격리
                log.warning("[anomaly-worker] 스코어링 실패, 다음 주기 재시도: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("[anomaly-worker] 이상 감지 워처 종료")
        raise
