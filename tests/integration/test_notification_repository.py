"""알림 레포지토리 통합 테스트 (NEW_PROACT01_ALERT01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵
테스트 데이터는 세션 내 flush만 하고 teardown rollback으로 DB 미오염
sync는 DB 전체 알람 대상이라 단정은 테스트 설비로 한정
소스는 변수별 알람 저널(EquipmentAlarm), 중복 억제는 설비+변수+알람+30분 버킷 단위
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.auth.models import User
from agentory.modules.notification import repository
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster

# 실데이터와 겹치지 않는 테스트 전용 식별자·시간대
LINE = "ZZZ-NOTI-LINE"
EQP = "ZZZ-NOTI-01"
T0 = datetime(2020, 2, 2, 0, 0, tzinfo=UTC)


def _alarm(metric: str, code: str, minute: int, hour: int = 0) -> EquipmentAlarm:
    # 변수별 알람 저널 1건, severity는 코드 접두 기준
    severity = "위험" if code.startswith("ERR") else "주의"
    return EquipmentAlarm(
        equipment_id=EQP,
        metric=metric,
        alarm_code=code,
        severity=severity,
        raised_at=T0.replace(hour=hour, minute=minute),
    )


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
        # 서로 다른 변수+코드 2건(같은 30분 버킷), 알림 2건 기대
        session.add_all(
            [
                _alarm("temperature", "ERR-401", 0),
                _alarm("pressure", "ERR-301", 15),
            ]
        )
        await session.flush()
        yield session
        await session.rollback()
    await engine.dispose()


def _mine(rows):
    # 테스트 설비 알림만 추림 (DB 타 데이터 배제)
    return [r for r in rows if r["equipment_id"] == EQP]


async def test_sync_creates_notifications_for_alarms(seeded_session):
    # 알람 저널 2건이 알림 2건으로 동기화
    created = await repository.sync_from_alarms(seeded_session)
    assert created >= 2  # 내 알람 2건 이상(DB 타 알람 포함 가능)
    codes = {r["alarm_code"] for r in _mine(await repository.fetch_notifications(seeded_session))}
    assert codes == {"ERR-401", "ERR-301"}


async def test_sync_is_idempotent(seeded_session):
    # 두 번째 sync는 신규 0건 (버킷 유니크 중복 방지)
    await repository.sync_from_alarms(seeded_session)
    assert await repository.sync_from_alarms(seeded_session) == 0


async def test_sync_dedup_by_30min_bucket(seeded_session):
    # 동일 설비+변수+알람은 30분 버킷당 1건, 다른 버킷은 별건 (NEW_PROACT01_ALERT03)
    # temperature/ERR-401을 같은 버킷(:20)과 다음 버킷(:35)에 추가
    seeded_session.add_all(
        [
            _alarm("temperature", "ERR-401", 20),  # :00과 같은 [00:00,00:30) 버킷
            _alarm("temperature", "ERR-401", 35),  # 다음 [00:30,01:00) 버킷
        ]
    )
    await seeded_session.flush()
    await repository.sync_from_alarms(seeded_session)
    temp = [
        r
        for r in _mine(await repository.fetch_notifications(seeded_session))
        if r["alarm_code"] == "ERR-401"
    ]
    # :00·:20은 1건으로 합쳐지고 :35가 별건, 총 2건
    assert len(temp) == 2
    # 첫 버킷 대표는 가장 이른 발생 시각(:00)
    first_bucket = [r for r in temp if r["occurred_at"].minute < 30]
    assert len(first_bucket) == 1
    assert first_bucket[0]["occurred_at"].minute == 0


async def test_sync_dedup_keys_on_metric(seeded_session):
    # 같은 코드라도 변수가 다르면 별건 (변수 조합이 중복 키에 포함됨)
    seeded_session.add_all(
        [
            _alarm("gas_flow", "WRN-501", 5),
            _alarm("rf_power", "WRN-501", 5),  # 같은 버킷·같은 코드, 다른 변수
        ]
    )
    await seeded_session.flush()
    await repository.sync_from_alarms(seeded_session)
    wrn501 = [
        r
        for r in _mine(await repository.fetch_notifications(seeded_session))
        if r["alarm_code"] == "WRN-501"
    ]
    assert len(wrn501) == 2  # 변수별로 분리


async def test_mark_read_individual_and_all(seeded_session):
    # 읽음은 사용자별 상태이므로 조회·갱신 모두 user_id 기준 (BE_NOTI01_SCOPE01)
    user = User(email="noti-repo@test.local", name="테스터", role="field_engineer", status="active")
    seeded_session.add(user)
    await seeded_session.flush()
    await repository.sync_from_alarms(seeded_session)
    mine = _mine(
        await repository.fetch_notifications(seeded_session, unread_only=True, user_id=user.id)
    )
    assert len(mine) == 2
    # 개별 읽음 성공, 미존재는 False
    assert await repository.mark_read(seeded_session, mine[0]["id"], user.id) is True
    assert await repository.mark_read(seeded_session, -1, user.id) is False
    # 일괄 읽음 후 내 설비 미읽음 0
    await repository.mark_all_read(seeded_session, user.id)
    assert (
        _mine(
            await repository.fetch_notifications(seeded_session, unread_only=True, user_id=user.id)
        )
        == []
    )


async def test_read_state_is_per_user(seeded_session):
    # 한 사용자가 읽어도 다른 사용자에게는 미읽음 유지 (BE_NOTI01_SCOPE01)
    reader = User(
        email="noti-reader@test.local", name="읽음", role="field_engineer", status="active"
    )
    other = User(email="noti-other@test.local", name="타인", role="field_engineer", status="active")
    seeded_session.add_all([reader, other])
    await seeded_session.flush()
    await repository.sync_from_alarms(seeded_session)
    await repository.mark_all_read(seeded_session, reader.id)

    assert (
        _mine(
            await repository.fetch_notifications(
                seeded_session, unread_only=True, user_id=reader.id
            )
        )
        == []
    )
    assert (
        len(
            _mine(
                await repository.fetch_notifications(
                    seeded_session, unread_only=True, user_id=other.id
                )
            )
        )
        == 2
    )


async def test_mark_unread_restores_unread_state(seeded_session):
    # 읽음 해제는 읽음 행 삭제, 다시 미읽음으로 복귀
    user = User(email="noti-toggle@test.local", name="토글", role="field_engineer", status="active")
    seeded_session.add(user)
    await seeded_session.flush()
    await repository.sync_from_alarms(seeded_session)
    target = _mine(await repository.fetch_notifications(seeded_session, user_id=user.id))[0]

    await repository.mark_read(seeded_session, target["id"], user.id)
    unread_ids = {
        r["id"]
        for r in _mine(
            await repository.fetch_notifications(seeded_session, unread_only=True, user_id=user.id)
        )
    }
    assert target["id"] not in unread_ids

    assert await repository.mark_unread(seeded_session, target["id"], user.id) is True
    unread_ids = {
        r["id"]
        for r in _mine(
            await repository.fetch_notifications(seeded_session, unread_only=True, user_id=user.id)
        )
    }
    assert target["id"] in unread_ids


async def test_fetch_after_id_ascending(seeded_session):
    # SSE 증분: after_id 이후 id만 오름차순
    await repository.sync_from_alarms(seeded_session)
    mine = sorted(
        _mine(await repository.fetch_notifications(seeded_session)), key=lambda r: r["id"]
    )
    first_id = mine[0]["id"]
    after = _mine(await repository.fetch_notifications(seeded_session, after_id=first_id))
    assert all(r["id"] > first_id for r in after)
    assert [r["id"] for r in after] == sorted(r["id"] for r in after)
