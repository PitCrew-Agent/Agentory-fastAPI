"""관리자 설비 수리·수리 이력 통합 테스트 (NEW_REPAIR01_REPAIR01/HISTORY01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵
수리 처리가 이력 적재 + 마스터 힐 래치·알람 해제를 함께 반영하는지, 작업 현황 조회의
정렬·필터·커서 페이지네이션·예외 경로를 검증
성공 경로가 commit을 하므로 ZZZ- 프리픽스 데이터를 앞뒤로 정리해 재실행 멱등성 확보
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.admin import service
from agentory.modules.admin.schemas import RepairRequest
from agentory.modules.auth.models import User
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

EQUIP_ID = "ZZZ-EQP-RPR"
EQUIP_ID2 = "ZZZ-EQP-RPR2"


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

    async def _cleanup():
        async with maker() as c:
            # 이력 먼저 삭제(FK), 이어 telemetry·마스터·유저 정리
            await c.execute(text("DELETE FROM equipment_repairs WHERE equipment_id LIKE 'ZZZ-%'"))
            await c.execute(
                text("DELETE FROM equipment_telemetries WHERE equipment_id LIKE 'ZZZ-%'")
            )
            await c.execute(text("DELETE FROM equipment_masters WHERE equipment_id LIKE 'ZZZ-%'"))
            await c.execute(text("DELETE FROM users WHERE email LIKE 'zzz-rpr-%'"))
            await c.commit()

    await _cleanup()
    async with maker() as s:
        yield s
        await s.rollback()
    await _cleanup()
    await engine.dispose()


async def _make_user(session, email="zzz-rpr-eng@example.com", name="ZZZ-ENG"):
    user = User(email=email, name=name, role="field_engineer")
    session.add(user)
    await session.flush()
    return user


async def _make_equipment(session, equipment_id=EQUIP_ID, *, alarm=None):
    session.add(
        EquipmentMaster(equipment_id=equipment_id, line_name="ZZZ-LINE", process_type="Etching")
    )
    await session.flush()
    if alarm is not None:
        # 수리 직전 알람 스냅샷 확인용 최신 tick
        session.add(EquipmentTelemetry(equipment_id=equipment_id, alarm_code=alarm))
        await session.flush()


async def test_repair_records_history_and_latches(session):
    # 수리 처리 시 이력 적재 + 책임자/직전 알람 기록 + 마스터 힐 래치·알람 해제 반영
    user = await _make_user(session)
    await _make_equipment(session, alarm="ERR-402")
    item = await service.repair_equipment(
        session, EQUIP_ID, RepairRequest(note="냉각 교체"), repaired_by=user.id
    )
    assert item is not None
    assert item.equipment_id == EQUIP_ID
    assert item.repaired_by == user.id
    assert item.repaired_by_name == "ZZZ-ENG"
    assert item.alarm_code_before == "ERR-402"  # 직전 알람 스냅샷
    assert item.note == "냉각 교체"
    # 마스터 힐 래치·알람 해제·점검일 갱신
    equip = await session.get(EquipmentMaster, EQUIP_ID)
    assert equip.repaired_at is not None
    assert equip.alarm_cleared_at is not None
    assert equip.last_inspection_at is not None


async def test_repair_unknown_equipment_returns_none(session):
    user = await _make_user(session, email="zzz-rpr-eng2@example.com")
    result = await service.repair_equipment(
        session, "ZZZ-NO-SUCH", RepairRequest(), repaired_by=user.id
    )
    assert result is None


async def test_list_repairs_orders_desc_and_filters(session):
    # 두 설비 수리 후 작업 현황이 수리 역순, equipment_id 필터가 해당 설비만
    user = await _make_user(session, email="zzz-rpr-eng3@example.com")
    await _make_equipment(session, EQUIP_ID)
    await _make_equipment(session, EQUIP_ID2)
    await service.repair_equipment(session, EQUIP_ID, RepairRequest(), repaired_by=user.id)
    await service.repair_equipment(session, EQUIP_ID2, RepairRequest(), repaired_by=user.id)

    page = await service.list_repairs(session, repaired_by=user.id, limit=10)
    ours = [r for r in page.items if r.equipment_id in (EQUIP_ID, EQUIP_ID2)]
    assert len(ours) == 2
    assert ours[0].repaired_at >= ours[1].repaired_at  # 수리 역순

    only = await service.list_repairs(session, equipment_id=EQUIP_ID2, limit=10)
    assert all(r.equipment_id == EQUIP_ID2 for r in only.items)
    assert len(only.items) == 1


async def test_list_repairs_cursor_pagination(session):
    # limit=1이면 has_more·커서 발급, 커서로 다음 페이지 조회
    user = await _make_user(session, email="zzz-rpr-eng4@example.com")
    await _make_equipment(session, EQUIP_ID)
    await _make_equipment(session, EQUIP_ID2)
    await service.repair_equipment(session, EQUIP_ID, RepairRequest(), repaired_by=user.id)
    await service.repair_equipment(session, EQUIP_ID2, RepairRequest(), repaired_by=user.id)

    first = await service.list_repairs(session, repaired_by=user.id, limit=1)
    assert len(first.items) == 1
    assert first.has_more is True
    assert first.next_cursor is not None
    second = await service.list_repairs(
        session, repaired_by=user.id, limit=1, before=first.next_cursor
    )
    assert len(second.items) == 1
    assert first.items[0].repaired_at >= second.items[0].repaired_at


async def test_list_repairs_bad_cursor(session):
    with pytest.raises(ValueError):
        await service.list_repairs(session, before="not-a-cursor!!")


async def test_list_equipment_repairs_not_found(session):
    # 미존재 설비는 None (라우터 404)
    assert await service.list_equipment_repairs(session, "ZZZ-NO-SUCH") is None


async def test_list_repairs_start_end_filter(session):
    # 미래 시작 필터면 방금 만든 수리가 제외됨
    user = await _make_user(session, email="zzz-rpr-eng5@example.com")
    await _make_equipment(session, EQUIP_ID)
    await service.repair_equipment(session, EQUIP_ID, RepairRequest(), repaired_by=user.id)
    future = datetime(2999, 1, 1, tzinfo=UTC)
    page = await service.list_repairs(session, equipment_id=EQUIP_ID, start=future, limit=10)
    assert page.items == []
