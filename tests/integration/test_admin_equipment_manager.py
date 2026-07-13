"""관리자 설비 책임자 지정 통합 테스트 (BE_ADMIN01_MANAGER01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵, teardown rollback으로 미오염
책임자 유저 지정·해제, 없는 유저·설비 예외 경로 검증
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.common.exceptions import ValidationError
from agentory.core.config import get_settings
from agentory.modules.admin import repository, service
from agentory.modules.admin.schemas import AssignManagerRequest
from agentory.modules.auth.models import User
from agentory.modules.telemetry.models import EquipmentMaster

# 실데이터와 겹치지 않는 테스트 전용 식별자
EQUIP_ID = "ZZZ-EQP-001"


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

    # 성공 경로가 commit을 하므로 rollback으로 안 지워지는 커밋 행을 앞뒤로 정리 (재실행 멱등성)
    async def _cleanup():
        async with maker() as c:
            await c.execute(text("DELETE FROM equipment_masters WHERE equipment_id LIKE 'ZZZ-%'"))
            await c.execute(text("DELETE FROM users WHERE email LIKE 'zzz-mgr-%'"))
            await c.commit()

    await _cleanup()
    async with maker() as s:
        yield s
        await s.rollback()
    await _cleanup()
    await engine.dispose()


async def _make_user(session, email="zzz-mgr-tester@example.com"):
    user = User(email=email, name="ZZZ-MANAGER", role="field_engineer")
    session.add(user)
    await session.flush()
    return user


async def _make_equipment(session, equipment_id=EQUIP_ID):
    equip = EquipmentMaster(equipment_id=equipment_id, line_name="ZZZ-LINE", process_type="식각")
    session.add(equip)
    await session.flush()
    return equip


async def test_assign_manager_sets_user(session):
    user = await _make_user(session)
    await _make_equipment(session)
    item = await service.assign_equipment_manager(
        session, EQUIP_ID, AssignManagerRequest(user_id=user.id)
    )
    assert item is not None
    assert item.manager is not None
    assert item.manager.id == user.id
    assert item.manager.name == "ZZZ-MANAGER"


async def test_unassign_manager_clears(session):
    user = await _make_user(session, email="zzz-mgr-tester2@example.com")
    equip = await _make_equipment(session)
    await repository.set_equipment_manager(session, equip.equipment_id, user.id)

    item = await service.assign_equipment_manager(
        session, EQUIP_ID, AssignManagerRequest(user_id=None)
    )
    assert item is not None
    assert item.manager is None


async def test_assign_unknown_user_raises(session):
    await _make_equipment(session)
    with pytest.raises(ValidationError):
        await service.assign_equipment_manager(session, EQUIP_ID, AssignManagerRequest(user_id=-1))


async def test_assign_unknown_equipment_returns_none(session):
    user = await _make_user(session, email="zzz-mgr-tester3@example.com")
    result = await service.assign_equipment_manager(
        session, "ZZZ-NO-SUCH-EQP", AssignManagerRequest(user_id=user.id)
    )
    assert result is None
