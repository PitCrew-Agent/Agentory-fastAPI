"""장애 대응 계획 컨텍스트 통합 테스트 (NEW_INCIDENT01_PLAN01)"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.incident import repository
from agentory.modules.notification.models import Notification
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

EQUIPMENT_ID = "ZZZ-INCIDENT-CTX"
OCCURRED_AT = datetime(2020, 4, 4, 4, 30, tzinfo=UTC)


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

    async with maker() as current:
        yield current
        await current.rollback()
    await engine.dispose()


async def test_fetch_incident_context_uses_source_and_normal_baseline(session):
    session.add(
        EquipmentMaster(
            equipment_id=EQUIPMENT_ID,
            line_name="ZZZ-LINE",
            process_type="Etching",
        )
    )
    baseline_one = EquipmentTelemetry(
        equipment_id=EQUIPMENT_ID,
        timestamp=OCCURRED_AT - timedelta(minutes=20),
        temperature=60,
        pressure=100,
    )
    baseline_two = EquipmentTelemetry(
        equipment_id=EQUIPMENT_ID,
        timestamp=OCCURRED_AT - timedelta(minutes=10),
        temperature=62,
        pressure=102,
    )
    incident = EquipmentTelemetry(
        equipment_id=EQUIPMENT_ID,
        timestamp=OCCURRED_AT,
        temperature=65,
        pressure=97,
        alarm_code="ERR-402",
    )
    session.add_all([baseline_one, baseline_two, incident])
    await session.flush()
    notification = Notification(
        occurred_at=OCCURRED_AT,
        equipment_id=EQUIPMENT_ID,
        alarm_code="ERR-402",
        message="냉각 계통 이상",
        bucket_start=OCCURRED_AT.replace(minute=0),
    )
    session.add(notification)
    await session.flush()

    context = await repository.fetch_incident_context(session, notification.id)

    assert context["incident"]["log_id"] == incident.log_id
    assert len(context["baseline"]) == 2
    assert context["equipment"]["process_type"] == "Etching"


async def test_fetch_incident_context_returns_none_for_unknown_notification(session):
    assert await repository.fetch_incident_context(session, -1) is None
