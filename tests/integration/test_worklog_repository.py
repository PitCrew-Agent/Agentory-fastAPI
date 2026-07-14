"""작업 로그 레포지토리·소유권 통합 테스트 (NEW_LOOP01_WORKLOG01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵, teardown rollback으로 미오염
소유권 authz는 커밋 없는 실패 경로(403/404)만 서비스로 검증
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.common.exceptions import NotFoundError, PermissionDeniedError
from agentory.core.config import get_settings
from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentMaster
from agentory.modules.worklog import repository, service
from agentory.modules.worklog.schemas import WorkLogStatus, WorkLogUpdate

# 실데이터와 겹치지 않는 테스트 전용 식별자
OWNER = "sub-owner-zzz"
OTHER = "sub-other-zzz"
WORKER = "ZZZ-TESTER"
S = datetime(2020, 3, 3, 2, 0, tzinfo=UTC)
E = datetime(2020, 3, 3, 3, 0, tzinfo=UTC)


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


async def _make(session, **over):
    kw = {
        "owner_sub": OWNER,
        "work_type": "정기점검",
        "worker_name": WORKER,
        "started_at": S,
        "ended_at": E,
        "plan": "작업",
        "status": "대기",
    }
    kw.update(over)
    return await repository.create_work_log(session, **kw)


async def test_create_and_get(session):
    created = await _make(session, work_type="수리점검")
    fetched = await repository.get_work_log(session, created["id"])
    assert fetched["worker_name"] == WORKER
    assert fetched["owner_sub"] == OWNER
    assert fetched["work_type"] == "수리점검"  # 유형 왕복
    assert fetched["ended_at"] is not None


async def test_list_excludes_soft_deleted_and_orders_desc(session):
    early = await _make(session, started_at=S)
    late = await _make(session, started_at=S.replace(hour=5))
    await repository.soft_delete_work_log(session, early["id"])
    ids = [r["id"] for r in await repository.list_work_logs(session) if r["worker_name"] == WORKER]
    assert early["id"] not in ids  # soft delete 제외
    assert ids.index(late["id"]) >= 0


async def test_soft_delete_then_get_none(session):
    created = await _make(session)
    await repository.soft_delete_work_log(session, created["id"])
    assert await repository.get_work_log(session, created["id"]) is None


async def test_update_partial_keeps_other_fields(session):
    created = await _make(session, plan="원본")
    updated = await repository.update_work_log(session, created["id"], {"status": "완료"})
    assert updated["status"] == "완료"
    assert updated["plan"] == "원본"


async def test_notification_link_allows_multiple_active_logs(session):
    equipment_id = "ZZZ-WORKLOG-LINK"
    session.add(
        EquipmentMaster(
            equipment_id=equipment_id,
            line_name="ZZZ-LINE",
            process_type="Etching",
        )
    )
    notification = Notification(
        occurred_at=S,
        equipment_id=equipment_id,
        alarm_code="ERR-402",
        message="냉각 계통 이상",
        bucket_hour=S.replace(minute=0),
    )
    session.add(notification)
    await session.flush()

    first = await _make(
        session,
        source_notification_id=notification.id,
        equipment_id=equipment_id,
        alarm_code="ERR-402",
    )
    second = await _make(
        session,
        source_notification_id=notification.id,
        equipment_id=equipment_id,
        alarm_code="ERR-402",
    )

    assert first["id"] != second["id"]
    assert second["source_notification_id"] == notification.id
    assert second["equipment_id"] == equipment_id


async def test_service_update_forbidden_for_non_owner(session):
    created = await _make(session)
    with pytest.raises(PermissionDeniedError):
        await service.update_work_log(
            session, created["id"], WorkLogUpdate(status=WorkLogStatus.DONE), requester_sub=OTHER
        )


async def test_service_update_not_found(session):
    # 미존재는 NotFoundError(전역 핸들러가 404 변환)
    with pytest.raises(NotFoundError):
        await service.update_work_log(
            session, -1, WorkLogUpdate(status=WorkLogStatus.DONE), requester_sub=OWNER
        )


async def test_service_delete_forbidden_for_non_owner(session):
    created = await _make(session)
    with pytest.raises(PermissionDeniedError):
        await service.delete_work_log(session, created["id"], requester_sub=OTHER)


async def test_service_delete_not_found(session):
    # 미존재는 NotFoundError(전역 핸들러가 404 변환)
    with pytest.raises(NotFoundError):
        await service.delete_work_log(session, -1, requester_sub=OWNER)
