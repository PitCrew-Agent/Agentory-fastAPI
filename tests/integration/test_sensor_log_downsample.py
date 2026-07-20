"""센서 로그 기간 조회 구간 포괄 통합 테스트 (BE_MCP02_TELEMETRY03)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
행수 상한을 넘는 기간을 요청해도 최신 구간만 잘려 오지 않고 전 구간이 포괄되는지 고정 (#191)
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.telemetry import repository
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry

# 전역 데이터와 겹치지 않는 테스트 전용 식별자·시간대
EQP = "ZZZ-DOWNSAMPLE-01"
LINE = "ZZZ-DOWNSAMPLE-LINE"
T0 = datetime(2001, 3, 1, 0, 0, tzinfo=UTC)
TICK_SECONDS = 5
# 상한(500)을 확실히 넘도록 2배 이상 적재
TICKS = 1200


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
        rows = []
        for tick in range(TICKS):
            # 마지막 근처에만 알람을 심어, 집계 후에도 대표 알람이 보존되는지 확인
            alarm = "ERR-401" if tick == TICKS - 3 else None
            rows.append(
                EquipmentTelemetry(
                    equipment_id=EQP,
                    timestamp=T0 + timedelta(seconds=TICK_SECONDS * tick),
                    temperature=60 + (tick % 10) * 0.1,
                    pressure=100,
                    rf_power=500,
                    gas_flow=50,
                    alarm_code=alarm,
                )
            )
        session.add_all(rows)
        await session.flush()
        yield session
        await session.rollback()
    await engine.dispose()


def _span_of(rows) -> timedelta:
    stamps = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    return max(stamps) - min(stamps)


async def test_short_range_returns_raw_rows(seeded_session):
    # 상한 안에 들어가는 기간은 원시 tick 그대로 반환
    end = T0 + timedelta(seconds=TICK_SECONDS * 100)
    rows = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=end, equipment_id=EQP
    )
    assert rows
    assert all(not r.get("aggregated") for r in rows)
    assert len(rows) <= get_settings().sensor_log_max_rows


async def test_long_range_covers_whole_period(seeded_session):
    # 상한을 넘는 기간은 최신 절단 대신 집계로 전 구간 포괄 (#191 회귀)
    end = T0 + timedelta(seconds=TICK_SECONDS * (TICKS - 1))
    requested = end - T0
    rows = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=end, equipment_id=EQP
    )
    assert rows
    assert all(r.get("aggregated") for r in rows)
    # 반환 구간이 요청 구간의 대부분을 덮어야 함 (버킷 경계로 인한 오차 허용)
    assert _span_of(rows) >= requested * 0.9


async def test_long_range_respects_row_budget(seeded_session):
    # 집계 결과도 행수 상한을 넘지 않아 LLM 컨텍스트 초과를 막음
    end = T0 + timedelta(seconds=TICK_SECONDS * (TICKS - 1))
    rows = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=end, equipment_id=EQP
    )
    assert len(rows) <= get_settings().sensor_log_max_rows


async def test_aggregated_row_carries_min_max_and_alarm(seeded_session):
    # 구간 평균과 함께 최소·최대를 실어 스파이크를 보존, 대표 알람도 유지
    end = T0 + timedelta(seconds=TICK_SECONDS * (TICKS - 1))
    rows = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=end, equipment_id=EQP
    )
    sample = rows[0]
    assert sample["bucket_seconds"] > 0
    assert sample["temperature_min"] <= sample["temperature"] <= sample["temperature_max"]
    assert "ERR-401" in {r["alarm_code"] for r in rows}


async def test_line_range_splits_budget_across_equipments(seeded_session):
    # 라인 조회도 설비별 예산을 나눠 총 반환량이 상한을 넘지 않음
    end = T0 + timedelta(seconds=TICK_SECONDS * (TICKS - 1))
    rows = await repository.fetch_sensor_logs(
        seeded_session, start_time=T0, end_time=end, line_name=LINE
    )
    assert rows
    assert {r["equipment_id"] for r in rows} == {EQP}
    assert len(rows) <= get_settings().sensor_log_max_rows
