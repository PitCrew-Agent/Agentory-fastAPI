"""알림 키셋 페이지네이션 통합 테스트 (NEW_PROACT01_ALERT02)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
전역 데이터와 겹치지 않도록 2000년 창의 전용 알림을 삽입해 격리
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.notification import repository, service
from agentory.modules.notification.models import Notification

EQP = "ZZZ-PAGE-01"
Y2000 = datetime(2000, 1, 1, 0, 0, tzinfo=UTC)  # 전역 데이터(2020+)와 격리된 창


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed3(session):
    # 시각 오름차순 n0 < n1 < n2 (같은 창), 미읽음
    rows = []
    for hour in range(3):
        n = Notification(
            occurred_at=Y2000.replace(hour=hour),
            equipment_id=EQP,
            alarm_code="ERR-000",
            message="테스트 알림",
            bucket_start=Y2000.replace(hour=hour),
        )
        session.add(n)
        rows.append(n)
    await session.flush()
    return rows  # rows[0] 가장 과거 .. rows[2] 최신


async def test_keyset_excludes_cursor_and_newer(session):
    n0, n1, _n2 = await _seed3(session)
    # before=(n1) 커서보다 과거만 → 내 알림 중 n0만
    page = await repository.fetch_notifications_page(
        session, before=(n1.occurred_at, n1.id), limit=100
    )
    mine = [r["id"] for r in page if r["equipment_id"] == EQP]
    assert mine == [n0.id]


async def test_limit_caps_page_size(session):
    await _seed3(session)
    # 내 창 위쪽 커서에서 limit=2면 최대 2건
    cursor = (Y2000.replace(hour=5), 0)
    page = await repository.fetch_notifications_page(session, before=cursor, limit=2)
    assert len(page) <= 2


async def test_walk_pages_via_next_cursor(session):
    n0, n1, n2 = await _seed3(session)
    # 최신 위쪽에서 시작해 next_cursor를 따라가며 내 알림 수집
    cursor = service._encode_cursor(Y2000.replace(hour=5), 0)
    collected: list[int] = []
    for _ in range(5):  # 무한 방지 안전 상한
        page = await service.list_notifications(session, before=cursor, limit=1)
        collected += [i.id for i in page.items if i.equipment_id == EQP]
        if not page.has_more:
            break
        cursor = page.next_cursor
    # 발생 역순으로 중복 없이 전부 수집
    assert collected == [n2.id, n1.id, n0.id]


async def test_unread_only_filters(session):
    n0, n1, n2 = await _seed3(session)
    await repository.mark_read(session, n2.id)
    cursor = (Y2000.replace(hour=5), 0)
    page = await repository.fetch_notifications_page(
        session, unread_only=True, before=cursor, limit=100
    )
    mine = {r["id"] for r in page if r["equipment_id"] == EQP}
    assert mine == {n0.id, n1.id}  # 읽은 n2 제외
