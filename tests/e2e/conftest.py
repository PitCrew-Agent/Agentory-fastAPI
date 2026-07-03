"""골든 시나리오 E2E 하네스 픽스처 (TEST_HARNESS)

실 LLM + 실 DB + 실 도구로 전체 파이프라인을 검증
도구는 repository를 감싼 in-process LangChain 도구로 붙여 MCP 서버 기동 없이 실행
OPENAI_API_KEY·DB 미가용 시 스킵
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from langchain_core.tools import tool
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph
from agentory.modules.telemetry import repository
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry
from mcp_realtime.server import _parse_time

# 시나리오 대상 설비·정상 마스터 (요구사항 §8.1)
EQUIPMENT = [
    ("EQP-001", "A-Line", "Deposition", "Zone-A", "Main-Tech 1"),
    ("EQP-002", "A-Line", "Etching", "Zone-A", "Main-Tech 1"),
    ("EQP-003", "B-Line", "Etching", "Zone-B", "Main-Tech 2"),
]
TARGET = "EQP-003"
# 최근 1시간 질의에 걸리도록 현재 기준 이상 추이 (온도 상승 + ERR-402)
ANOMALY = [("42.0", None), ("53.0", "ERR-402"), ("61.0", "ERR-402"), ("65.0", "ERR-402")]


def _realtime_tools(maker: async_sessionmaker) -> list:
    # repository를 감싼 in-process 도구, MCP 서버의 도구와 이름·의미 동일
    @tool
    async def get_sensor_logs(
        start_time: str,
        end_time: str,
        equipment_id: str | None = None,
        line_name: str | None = None,
    ) -> list:
        """라인/설비의 기간 내 센서·에러 로그 반환 (equipment_id 또는 line_name 필수)"""
        async with maker() as s:
            return await repository.fetch_sensor_logs(
                s,
                start_time=_parse_time(start_time, "start_time"),
                end_time=_parse_time(end_time, "end_time"),
                equipment_id=equipment_id,
                line_name=line_name,
            )

    @tool
    async def get_alarm_history(
        equipment_id: str, start_time: str, end_time: str, alarm_code: str | None = None
    ) -> list:
        """지정 기간 알람 코드별 발생 횟수·최초/최근 시각 집계"""
        async with maker() as s:
            return await repository.fetch_alarm_history(
                s,
                equipment_id=equipment_id,
                start_time=_parse_time(start_time, "start_time"),
                end_time=_parse_time(end_time, "end_time"),
                alarm_code=alarm_code,
            )

    @tool
    async def get_equipment_metadata(
        equipment_id: str | None = None, line_name: str | None = None
    ) -> list:
        """설비 위치·부서·공정 단계 조회, 인자 없으면 전체 설비 목록 반환"""
        async with maker() as s:
            return await repository.fetch_equipment_metadata(
                s, equipment_id=equipment_id, line_name=line_name
            )

    return [get_sensor_logs, get_alarm_history, get_equipment_metadata]


async def _seed_scenario(maker: async_sessionmaker) -> list[int]:
    # 설비 마스터 보강 + 대상 설비 최근 이상 텔레메트리 주입, 삽입 log_id 반환(정리용)
    async with maker() as s:
        existing = set(await s.scalars(select(EquipmentMaster.equipment_id)))
        for eid, line, proc, loc, dept in EQUIPMENT:
            if eid not in existing:
                s.add(
                    EquipmentMaster(
                        equipment_id=eid,
                        line_name=line,
                        process_type=proc,
                        location=loc,
                        manager_dept=dept,
                    )
                )
        base = datetime.now(UTC) - timedelta(minutes=30)
        rows = [
            EquipmentTelemetry(
                equipment_id=TARGET,
                timestamp=base + timedelta(minutes=i * 5),
                temperature=Decimal(temp),
                pressure=Decimal("0.90"),
                alarm_code=alarm,
            )
            for i, (temp, alarm) in enumerate(ANOMALY)
        ]
        s.add_all(rows)
        await s.commit()
        return [r.log_id for r in rows]


async def _cleanup(maker: async_sessionmaker, log_ids: list[int]) -> None:
    # 주입한 텔레메트리만 제거해 DB 오염 방지
    async with maker() as s:
        await s.execute(delete(EquipmentTelemetry).where(EquipmentTelemetry.log_id.in_(log_ids)))
        await s.commit()


@pytest.fixture
async def e2e_runner():
    settings = get_settings()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY 없음, E2E 스킵")

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, E2E 스킵")

    log_ids = await _seed_scenario(maker)
    tools = {"realtime": _realtime_tools(maker), "knowledge": []}
    graph = await build_agent_graph(
        router_llm=get_chat_model("router"),
        worker_llm=get_chat_model("worker"),
        finalizer_llm=get_chat_model("finalizer"),
        tools_by_server=tools,
        grounding_enabled=False,
    )

    async def run(query: str) -> dict:
        state = initial_state(query, [])
        return await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})

    # knowledge 도구 부재 여부를 전달해 지식 의존 검증을 조건부 처리
    yield run, {"knowledge_available": bool(tools["knowledge"])}

    await _cleanup(maker, log_ids)
    await engine.dispose()
