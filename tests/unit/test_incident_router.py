"""장애 대응 계획 라우터 예외 테스트 (NEW_INCIDENT01_PLAN01)"""

import pytest

from agentory.common.exceptions import NotFoundError
from agentory.modules.incident import router as incident_router
from agentory.modules.incident.schemas import IncidentPlanCreate


@pytest.mark.asyncio
async def test_create_returns_not_found(monkeypatch):
    async def fake_create(session, notification_id):
        raise NotFoundError("error.notification.not_found", params={"id": notification_id})

    monkeypatch.setattr(incident_router.service, "create_incident_plan", fake_create)
    with pytest.raises(NotFoundError) as exc_info:
        await incident_router.create_incident_plan(
            IncidentPlanCreate(notification_id=999),
            _={},
            session=None,
        )
    assert exc_info.value.http_status == 404
