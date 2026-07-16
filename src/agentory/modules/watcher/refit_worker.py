"""이상 감지 모델 주기 재적합 워처 (BE_ANOM01_DRIFT01)

anomaly_refit_interval_hours마다 staleness 초과 모델을 최근 정상 데이터로 재적합
완만한 정상 이동 추종, 개별 주기 실패는 격리해 루프 유지 (sync_worker 패턴)
"""

import asyncio
import logging

from agentory.core.config import Settings
from agentory.modules.watcher.fit import fit_all

log = logging.getLogger("anomaly-refit")


async def run_refit_loop(settings: Settings) -> None:
    """취소(앱 종료)까지 주기 재적합 반복, staleness 초과 모델만 최근성 경계로 재적합"""
    interval = settings.anomaly_refit_interval_hours * 3600
    log.info(
        "[anomaly-refit] 재적합 워처 시작, 점검 주기 %.1f시간",
        settings.anomaly_refit_interval_hours,
    )
    try:
        while True:
            try:
                refitted = await fit_all(
                    settings, recency_days=settings.anomaly_refit_recency_days, only_stale=True
                )
                if refitted:
                    log.info("[anomaly-refit] %d개 공정 유형 재적합", len(refitted))
            except Exception as exc:  # 일시적 DB·적합 오류가 루프를 죽이지 않도록 격리
                log.warning("[anomaly-refit] 재적합 실패, 다음 주기 재시도: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("[anomaly-refit] 재적합 워처 종료")
        raise
