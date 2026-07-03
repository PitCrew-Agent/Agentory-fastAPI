"""텔레메트리 레포지토리 통합 테스트 (BE_MCP02/03)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵
테스트 데이터는 세션 내에서 flush만 하고 teardown에서 rollback하여 DB를 오염시키지 않음
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.telemetry import repository
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

# 실데이터와 겹치지 않는 테스트 전용 식별자·시간대
LINE = "ZZZ-TEST-LINE"
EQP = "ZZZ-TEST-01"
T0 = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)


@pytest.fixture
async def seeded_session():
    # 테스트별 자체 엔진(NullPool), 전역 엔진의 이벤트 루프 바인딩 문제 회피
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as session:
        session.add(
            EquipmentMaster(
                equipment_id=EQP,
                line_name=LINE,
                process_type="Etching",
                location="Zone-Z",
                manager_dept="Test-Dept",
            )
        )
        await session.flush()
        session.add_all(
            [
                EquipmentTelemetry(
                    equipment_id=EQP,
                    timestamp=T0.replace(minute=m),
                    temperature=Decimal(str(temp)),
                    pressure=Decimal("1.00"),
                    alarm_code=alarm,
                )
                for m, temp, alarm in [
                    (0, "42.0", None),
                    (15, "55.0", "ERR-402"),
                    (30, "61.0", "ERR-402"),
                ]
            ]
        )
        await session.flush()
        yield session
        await session.rollback()
    await engine.dispose()


async def test_fetch_sensor_logs_by_equipment(seeded_session):
    logs = await repository.fetch_sensor_logs(
        seeded_session,
        start_time=T0,
        end_time=T0.replace(minute=59),
        equipment_id=EQP,
    )
    assert len(logs) == 3
    assert logs[0]["timestamp"] < logs[-1]["timestamp"]  # 시간순
    assert logs[0]["temperature"] == 42.0


async def test_fetch_sensor_logs_by_line(seeded_session):
    logs = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=T0.replace(minute=59), line_name=LINE
    )
    assert len(logs) == 3


async def test_fetch_sensor_logs_empty_range(seeded_session):
    logs = await repository.fetch_sensor_logs(
        seeded_session,
        start_time=datetime(2019, 1, 1, tzinfo=UTC),
        end_time=datetime(2019, 1, 2, tzinfo=UTC),
        equipment_id=EQP,
    )
    assert logs == []


async def test_fetch_alarm_history_aggregates(seeded_session):
    alarms = await repository.fetch_alarm_history(
        seeded_session, equipment_id=EQP, start_time=T0, end_time=T0.replace(minute=59)
    )
    assert len(alarms) == 1
    assert alarms[0]["alarm_code"] == "ERR-402"
    assert alarms[0]["count"] == 2  # NULL 알람은 집계 제외


async def test_fetch_equipment_metadata(seeded_session):
    rows = await repository.fetch_equipment_metadata(seeded_session, equipment_id=EQP)
    assert len(rows) == 1
    assert rows[0]["process_type"] == "Etching"
    assert rows[0]["manager_dept"] == "Test-Dept"


async def test_fetch_equipment_metadata_not_found(seeded_session):
    rows = await repository.fetch_equipment_metadata(seeded_session, equipment_id="NO-SUCH")
    assert rows == []
