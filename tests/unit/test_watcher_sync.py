"""알림 동기화 워처 루프 단위 테스트 (NEW_PROACT01_DETECT01)

DB 없이 루프 제어 흐름만 검증, _sync_once·sleep을 대체해 결정론적으로 확인
"""

import asyncio

import pytest

from agentory.modules.watcher import sync_worker


async def test_sync_loop_repeats_until_cancelled(monkeypatch):
    # 취소 전까지 주기 동기화를 반복하고, CancelledError는 상위로 전파
    calls: list[int] = []

    async def fake_sync() -> int:
        calls.append(len(calls))
        return 0

    sleeps: list[float] = []

    async def fake_sleep(interval: float) -> None:
        sleeps.append(interval)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(sync_worker, "_sync_once", fake_sync)
    monkeypatch.setattr(sync_worker.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await sync_worker.run_sync_loop(5.0)

    assert len(calls) == 3
    assert sleeps == [5.0, 5.0, 5.0]


async def test_sync_loop_isolates_sync_errors(monkeypatch):
    # 개별 주기 예외(일시 DB 오류 등)에도 루프가 죽지 않고 다음 주기 계속
    calls: list[int] = []

    async def flaky_sync() -> int:
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("일시 오류")
        return 1

    sleeps: list[float] = []

    async def fake_sleep(interval: float) -> None:
        sleeps.append(interval)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(sync_worker, "_sync_once", flaky_sync)
    monkeypatch.setattr(sync_worker.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await sync_worker.run_sync_loop(5.0)

    # 첫 주기 예외 후에도 두 번째 주기가 실행됨
    assert len(calls) == 2
