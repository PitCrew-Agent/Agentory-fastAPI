"""알림 담당 라인 스코핑 통합 테스트 (BE_NOTI01_SCOPE01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵
테스트 데이터는 세션 내 flush만 하고 teardown rollback으로 DB 미오염
담당 라인·비담당 라인·라인 미배정 3케이스로 조회 스코프를 고정
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.admin.models import Line, UserLine
from agentory.modules.auth.models import User
from agentory.modules.notification import repository, service
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster

# 실데이터와 겹치지 않는 테스트 전용 식별자·시간대
MINE_LINE = "ZZZ-SCOPE-MINE"
OTHER_LINE = "ZZZ-SCOPE-OTHER"
MINE_EQP = "ZZZ-SCOPE-01"
OTHER_EQP = "ZZZ-SCOPE-02"
T0 = datetime(2020, 4, 4, 0, 0, tzinfo=UTC)


def _alarm(equipment_id: str, code: str, minute: int) -> EquipmentAlarm:
    # 변수별 알람 저널 1건, severity는 코드 접두 기준
    return EquipmentAlarm(
        equipment_id=equipment_id,
        metric="temperature",
        alarm_code=code,
        severity="위험",
        raised_at=T0.replace(minute=minute),
    )


@pytest.fixture
async def scoped_session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as session:
        # 라인 2개, 각 라인에 설비 1대씩
        mine = Line(code=MINE_LINE, name=MINE_LINE)
        other = Line(code=OTHER_LINE, name=OTHER_LINE)
        session.add_all([mine, other])
        session.add_all(
            [
                EquipmentMaster(equipment_id=MINE_EQP, line_name=MINE_LINE, process_type="Etching"),
                EquipmentMaster(
                    equipment_id=OTHER_EQP, line_name=OTHER_LINE, process_type="Etching"
                ),
            ]
        )
        # 담당자는 MINE_LINE만 배정, 미배정 사용자는 라인 없음
        assigned = User(
            email="scope-assigned@test.local", name="담당자", role="field_engineer", status="active"
        )
        unassigned = User(
            email="scope-none@test.local", name="미배정", role="field_engineer", status="active"
        )
        admin = User(email="scope-admin@test.local", name="관리자", role="admin", status="active")
        session.add_all([assigned, unassigned, admin])
        await session.flush()
        session.add(UserLine(user_id=assigned.id, line_id=mine.id))
        session.add_all([_alarm(MINE_EQP, "ERR-401", 0), _alarm(OTHER_EQP, "ERR-402", 5)])
        await session.flush()
        await repository.sync_from_alarms(session)
        yield session, assigned, unassigned, admin
        await session.rollback()
    await engine.dispose()


def _test_equipment(rows):
    # 테스트 설비 알림만 추림 (DB 타 데이터 배제)
    return [r for r in rows if r["equipment_id"] in (MINE_EQP, OTHER_EQP)]


async def test_sync_fills_line_name_from_equipment_master(scoped_session):
    # 알림 적재 시 설비 마스터의 line_name을 함께 보관
    session, *_ = scoped_session
    rows = _test_equipment(await repository.fetch_notifications(session))
    by_equipment = {r["equipment_id"]: r["line_name"] for r in rows}
    assert by_equipment[MINE_EQP] == MINE_LINE
    assert by_equipment[OTHER_EQP] == OTHER_LINE


async def test_assigned_user_sees_only_own_line(scoped_session):
    # 담당 라인 알림만 조회, 비담당 라인 알림은 제외
    session, assigned, *_ = scoped_session
    line_names = await service.scope_line_names(
        session, {"user_id": assigned.id, "role": "field_engineer"}
    )
    assert line_names == [MINE_LINE]
    rows = _test_equipment(await repository.fetch_notifications(session, line_names=line_names))
    assert [r["equipment_id"] for r in rows] == [MINE_EQP]


async def test_unassigned_user_sees_nothing(scoped_session):
    # 라인 미배정 사용자는 알림 없음 (전체 노출 fail-open 금지)
    session, _, unassigned, _ = scoped_session
    line_names = await service.scope_line_names(
        session, {"user_id": unassigned.id, "role": "field_engineer"}
    )
    assert line_names == []
    assert await repository.fetch_notifications(session, line_names=line_names) == []


async def test_admin_sees_all_lines(scoped_session):
    # 관리자는 스코핑 예외, 전 라인 조회
    session, _, _, admin = scoped_session
    line_names = await service.scope_line_names(session, {"user_id": admin.id, "role": "admin"})
    assert line_names is None
    rows = _test_equipment(await repository.fetch_notifications(session, line_names=line_names))
    assert {r["equipment_id"] for r in rows} == {MINE_EQP, OTHER_EQP}


async def test_page_scoped_to_assigned_line(scoped_session):
    # 이력 페이지네이션도 담당 라인으로 스코핑
    session, assigned, *_ = scoped_session
    rows = _test_equipment(
        await repository.fetch_notifications_page(session, limit=50, line_names=[MINE_LINE])
    )
    assert [r["equipment_id"] for r in rows] == [MINE_EQP]


async def test_count_scoped_to_assigned_line(scoped_session):
    # 총 건수도 담당 라인 기준, 화면 페이지 수 표기가 스코프와 일치
    session, *_ = scoped_session
    mine = await repository.count_notifications(session, line_names=[MINE_LINE])
    both = await repository.count_notifications(session, line_names=[MINE_LINE, OTHER_LINE])
    assert both == mine + await repository.count_notifications(session, line_names=[OTHER_LINE])
    assert mine >= 1


async def test_mark_read_rejects_other_line(scoped_session):
    # 담당 라인 밖 알림은 개별 읽음 처리 불가
    session, assigned, *_ = scoped_session
    other = next(
        r
        for r in _test_equipment(await repository.fetch_notifications(session))
        if r["equipment_id"] == OTHER_EQP
    )
    assert (
        await repository.mark_read(session, other["id"], assigned.id, line_names=[MINE_LINE])
        is False
    )
    assert (
        await repository.mark_read(session, other["id"], assigned.id, line_names=[OTHER_LINE])
        is True
    )


async def test_mark_all_read_scoped_to_assigned_line(scoped_session):
    # 일괄 읽음은 담당 라인에만 적용, 타 라인 알림은 미읽음 유지
    session, assigned, *_ = scoped_session
    await repository.mark_all_read(session, assigned.id, line_names=[MINE_LINE])
    unread = _test_equipment(
        await repository.fetch_notifications(session, unread_only=True, user_id=assigned.id)
    )
    assert [r["equipment_id"] for r in unread] == [OTHER_EQP]
