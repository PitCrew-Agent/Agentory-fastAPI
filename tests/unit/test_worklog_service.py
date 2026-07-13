"""작업 로그 알림 연결 서비스 단위 테스트 (NEW_INCIDENT01_PLAN01)"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agentory.common.exceptions import NotFoundError
from agentory.modules.worklog import service
from agentory.modules.worklog.schemas import WorkLogCreate, WorkLogType

STARTED_AT = datetime(2026, 7, 13, 1, 31, tzinfo=UTC)
CREATED_AT = datetime(2026, 7, 13, 1, 32, tzinfo=UTC)


def _payload(notification_id=42):
    return WorkLogCreate(
        work_type=WorkLogType.EMERGENCY,
        started_at=STARTED_AT,
        content="장애 대응",
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
            "content": fields["content"],
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
            "content": fields["content"],
            "status": fields["status"],
            "created_at": CREATED_AT,
        }

    monkeypatch.setattr(service.repository, "create_work_log", fake_create)
    session = AsyncMock()
    payload = WorkLogCreate(
        work_type=WorkLogType.REGULAR,
        started_at=STARTED_AT,
        content="임의 점검",
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
