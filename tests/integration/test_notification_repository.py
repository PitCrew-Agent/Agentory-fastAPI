"""알림 레포지토리 통합 테스트 (NEW_PROACT01_ALERT01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵
테스트 데이터는 세션 내 flush만 하고 teardown rollback으로 DB 미오염
sync는 DB 전체 알람 대상이라 단정은 테스트 설비로 한정
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.notification import repository
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

# 실데이터와 겹치지 않는 테스트 전용 식별자·시간대
LINE = "ZZZ-NOTI-LINE"
EQP = "ZZZ-NOTI-01"
T0 = datetime(2020, 2, 2, 0, 0, tzinfo=UTC)


@pytest.fixture
async def seeded_session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as session:
        session.add(EquipmentMaster(equipment_id=EQP, line_name=LINE, process_type="Etching"))
        await session.flush()
        session.add_all(
            EquipmentTelemetry(
                equipment_id=EQP,
                timestamp=T0.replace(minute=m),
                temperature=Decimal("60.0"),
                alarm_code=alarm,
            )
            for m, alarm in [(0, None), (15, "ERR-402"), (30, "WRN-801")]
        )
        await session.flush()
        yield session
        await session.rollback()
    await engine.dispose()


def _mine(rows):
    # 테스트 설비 알림만 추림 (DB 타 데이터 배제)
    return [r for r in rows if r["equipment_id"] == EQP]


async def test_sync_creates_notifications_for_alarms(seeded_session):
    # 알람 있는 로그(2건)만 알림 생성, NULL 알람 제외
    created = await repository.sync_from_telemetry(seeded_session)
    assert created >= 2  # 내 알람 2건 이상(DB 타 알람 포함 가능)
    codes = {r["alarm_code"] for r in _mine(await repository.fetch_notifications(seeded_session))}
    assert codes == {"ERR-402", "WRN-801"}


async def test_sync_is_idempotent(seeded_session):
    # 두 번째 sync는 신규 0건 (버킷 유니크 중복 방지)
    await repository.sync_from_telemetry(seeded_session)
    assert await repository.sync_from_telemetry(seeded_session) == 0


async def test_sync_dedup_by_hour_bucket(seeded_session):
    # 동일 설비+알람은 시간 버킷당 1건, 다른 시간대는 별건 (NEW_PROACT01_ALERT03)
    # 픽스처의 ERR-402(00:15)와 같은 시간대 중복(00:45), 다음 시간대(01:05) 추가
    seeded_session.add_all(
        EquipmentTelemetry(
            equipment_id=EQP,
            timestamp=ts,
            temperature=Decimal("60.0"),
            alarm_code="ERR-402",
        )
        for ts in (T0.replace(minute=45), T0.replace(hour=1, minute=5))
    )
    await seeded_session.flush()
    await repository.sync_from_telemetry(seeded_session)
    err402 = [
        r
        for r in _mine(await repository.fetch_notifications(seeded_session))
        if r["alarm_code"] == "ERR-402"
    ]
    # 00시대 :15·:45는 1건으로 합쳐지고 01시대가 별건, 총 2건
    assert len(err402) == 2
    # 같은 버킷 대표는 가장 이른 발생 시각(00:15)
    hour0 = [r for r in err402 if r["occurred_at"].hour == 0]
    assert len(hour0) == 1
    assert hour0[0]["occurred_at"].minute == 15


async def test_mark_read_individual_and_all(seeded_session):
    await repository.sync_from_telemetry(seeded_session)
    mine = _mine(await repository.fetch_notifications(seeded_session, unread_only=True))
    assert len(mine) == 2
    # 개별 읽음 성공, 미존재는 False
    assert await repository.mark_read(seeded_session, mine[0]["id"]) is True
    assert await repository.mark_read(seeded_session, -1) is False
    # 일괄 읽음 후 내 설비 미읽음 0
    await repository.mark_all_read(seeded_session)
    assert _mine(await repository.fetch_notifications(seeded_session, unread_only=True)) == []


async def test_fetch_after_id_ascending(seeded_session):
    # SSE 증분: after_id 이후 id만 오름차순
    await repository.sync_from_telemetry(seeded_session)
    mine = sorted(
        _mine(await repository.fetch_notifications(seeded_session)), key=lambda r: r["id"]
    )
    first_id = mine[0]["id"]
    after = _mine(await repository.fetch_notifications(seeded_session, after_id=first_id))
    assert all(r["id"] > first_id for r in after)
    assert [r["id"] for r in after] == sorted(r["id"] for r in after)
