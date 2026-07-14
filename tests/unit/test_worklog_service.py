"""작업 로그 알림 연결 서비스 단위 테스트 (NEW_INCIDENT01_PLAN01)"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agentory.common.exceptions import NotFoundError
from agentory.modules.worklog import service
from agentory.modules.worklog.schemas import WorkLogComplete, WorkLogCreate, WorkLogType

STARTED_AT = datetime(2026, 7, 13, 1, 31, tzinfo=UTC)
CREATED_AT = datetime(2026, 7, 13, 1, 32, tzinfo=UTC)


def _payload(notification_id=42):
    return WorkLogCreate(
        work_type=WorkLogType.EMERGENCY,
        started_at=STARTED_AT,
        plan="장애 대응",
        source_notification_id=notification_id,
    )


@pytest.mark.asyncio
async def test_create_derives_equipment_and_alarm_from_notification(monkeypatch):
    async def fake_notification(session, notification_id):
        return {"id": notification_id, "equipment_id": "EQP-A05", "alarm_code": "ERR-402"}

    async def fake_create(session, **fields):
        return {
            "id": 1,
            "owner_sub": fields["owner_sub"],
            "work_type": fields["work_type"],
            "worker_name": fields["worker_name"],
            "source_notification_id": fields["source_notification_id"],
            "equipment_id": fields["equipment_id"],
            "alarm_code": fields["alarm_code"],
            "started_at": fields["started_at"],
            "ended_at": fields["ended_at"],
            "plan": fields["plan"],
            "status": fields["status"],
            "created_at": CREATED_AT,
        }

    monkeypatch.setattr(service.repository, "get_notification_reference", fake_notification)
    monkeypatch.setattr(service.repository, "create_work_log", fake_create)
    session = AsyncMock()

    result = await service.create_work_log(
        session,
        _payload(),
        owner_sub="worker@test",
        worker_name="작업자",
    )

    assert result.source_notification_id == 42
    assert result.equipment_id == "EQP-A05"
    assert result.alarm_code == "ERR-402"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rejects_unknown_notification(monkeypatch):
    async def fake_notification(session, notification_id):
        return None

    monkeypatch.setattr(service.repository, "get_notification_reference", fake_notification)
    with pytest.raises(NotFoundError):
        await service.create_work_log(
            AsyncMock(),
            _payload(999),
            owner_sub="worker@test",
            worker_name="작업자",
        )


@pytest.mark.asyncio
async def test_create_allows_manual_log_without_notification(monkeypatch):
    async def fake_create(session, **fields):
        return {
            "id": 2,
            "owner_sub": fields["owner_sub"],
            "work_type": fields["work_type"],
            "worker_name": fields["worker_name"],
            "source_notification_id": fields["source_notification_id"],
            "equipment_id": fields["equipment_id"],
            "alarm_code": fields["alarm_code"],
            "started_at": fields["started_at"],
            "ended_at": fields["ended_at"],
            "plan": fields["plan"],
            "status": fields["status"],
            "created_at": CREATED_AT,
        }

    monkeypatch.setattr(service.repository, "create_work_log", fake_create)
    session = AsyncMock()
    payload = WorkLogCreate(
        work_type=WorkLogType.REGULAR,
        started_at=STARTED_AT,
        plan="임의 점검",
    )

    result = await service.create_work_log(
        session,
        payload,
        owner_sub="worker@test",
        worker_name="작업자",
    )

    assert result.source_notification_id is None
    assert result.equipment_id is None
    assert result.alarm_code is None
    session.commit.assert_awaited_once()


# --- 작업 완료 처리 + 유형별 도메인 적재 (NEW_LOOP01_WORKLOG01) ---


def _log(work_type="기타", equipment_id=None, owner="worker@test"):
    return {
        "id": 5,
        "owner_sub": owner,
        "work_type": work_type,
        "worker_name": "작업자",
        "source_notification_id": None,
        "equipment_id": equipment_id,
        "alarm_code": None,
        "started_at": STARTED_AT,
        "ended_at": None,
        "plan": "계획",
        "completion": None,
        "completed_at": None,
        "status": "진행중",
        "created_at": CREATED_AT,
    }


def _patch_complete(monkeypatch, log):
    async def fake_get(session, work_log_id):
        return log

    async def fake_complete(session, work_log_id, *, completion, completed_at):
        done = dict(log)
        done.update(completion=completion, completed_at=completed_at, status="완료")
        return done

    monkeypatch.setattr(service.repository, "get_work_log", fake_get)
    monkeypatch.setattr(service.repository, "complete_work_log", fake_complete)


@pytest.mark.asyncio
async def test_complete_sets_completion_without_dispatch_when_no_equipment(monkeypatch):
    # 설비 미연결 로그는 완료 상태·내용만 기록, 도메인 적재 없음
    create_repair = AsyncMock()
    _patch_complete(monkeypatch, _log(work_type="기타", equipment_id=None))
    monkeypatch.setattr(service.admin_repository, "create_repair", create_repair)
    session = AsyncMock()

    result = await service.complete_work_log(
        session, 5, WorkLogComplete(completion="완료함"), requester_sub="worker@test", repaired_by=7
    )

    assert result.status == "완료"
    assert result.completion == "완료함"
    assert result.completed_at is not None
    create_repair.assert_not_awaited()
    session.execute.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_repair_type_records_repair(monkeypatch):
    # 수리류(긴급수리) 완료는 equipment_repairs에 수리 이력 적재
    create_repair = AsyncMock()
    _patch_complete(monkeypatch, _log(work_type="긴급수리", equipment_id="EQP-A05"))
    monkeypatch.setattr(service.admin_repository, "create_repair", create_repair)
    session = AsyncMock()

    await service.complete_work_log(
        session,
        5,
        WorkLogComplete(completion="밸브 교체"),
        requester_sub="worker@test",
        repaired_by=7,
    )

    create_repair.assert_awaited_once()
    assert create_repair.await_args.kwargs["equipment_id"] == "EQP-A05"
    assert create_repair.await_args.kwargs["repaired_by"] == 7


@pytest.mark.asyncio
async def test_complete_inspection_type_updates_last_inspection(monkeypatch):
    # 점검류(정기점검) 완료는 설비 최신 점검일 갱신(session.execute), 수리 이력 적재 없음
    create_repair = AsyncMock()
    _patch_complete(monkeypatch, _log(work_type="정기점검", equipment_id="EQP-A05"))
    monkeypatch.setattr(service.admin_repository, "create_repair", create_repair)
    session = AsyncMock()

    await service.complete_work_log(
        session,
        5,
        WorkLogComplete(completion="점검 완료"),
        requester_sub="worker@test",
        repaired_by=7,
    )

    create_repair.assert_not_awaited()
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()
