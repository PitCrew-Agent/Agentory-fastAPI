"""골든 시나리오 E2E 하네스 픽스처 (TEST_HARNESS)

실 LLM + 실 DB + 실 도구로 전체 파이프라인을 검증
도구는 repository를 감싼 in-process LangChain 도구로 붙여 MCP 서버 기동 없이 실행
OPENAI_API_KEY·DB 미가용 시 스킵
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from langchain_core.tools import tool
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph
from agentory.modules.rag.embedding import get_embedder
from agentory.modules.rag.store.models import KnowledgeChunk
from agentory.modules.rag.store.pgvector import PgVectorStore
from agentory.modules.telemetry import repository
from agentory.modules.telemetry.models import (
    EquipmentAlarm,
    EquipmentMaster,
    EquipmentTelemetry,
)
from mcp_knowledge.server import _above_threshold
from mcp_realtime.server import _parse_time

# 시나리오 대상 설비·정상 마스터 (요구사항 §8.1)
EQUIPMENT = [
    ("EQP-001", "A라인", "Deposition", "Zone-A", "Main-Tech 1"),
    ("EQP-002", "A라인", "Etching", "Zone-A", "Main-Tech 1"),
    ("EQP-003", "B라인", "Etching", "Zone-B", "Main-Tech 2"),
]
TARGET = "EQP-003"
# 최근 1시간 질의에 걸리도록 현재 기준 이상 추이 (온도 상승 + ERR-402)
ANOMALY = [("42.0", None), ("53.0", "ERR-402"), ("61.0", "ERR-402"), ("65.0", "ERR-402")]

# 라이브 텔레메트리 라이터(연속 시뮬레이터) 감지 여유 (TEST_HARNESS)
# 시뮬레이터 기본 주기(5s)보다 넉넉히 커서 tick 사이에 프로브가 걸려도 오탐 없이 활성 라이터 판정
LIVE_WRITER_WINDOW = timedelta(seconds=30)


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


def _knowledge_tools(maker: async_sessionmaker) -> list:
    # MCP knowledge 서버의 search_manuals와 이름·의미 동일한 in-process 도구 (BE_MCP04_RAG01)
    @tool
    async def search_manuals(
        query: str, top_k: int | None = None, equipment_type: str | None = None
    ) -> list:
        """자연어 질문으로 매뉴얼 벡터 컬렉션 유사도 Top-K 검색, [{doc_id, content, score}] 반환"""
        settings = get_settings()
        resolved_top_k = settings.rag_search_top_k if top_k is None else top_k
        embedding = await get_embedder().embed_query(query)
        results = await PgVectorStore(maker).search(
            embedding, top_k=resolved_top_k, equipment_type=equipment_type
        )
        return _above_threshold(results, threshold=settings.rag_search_min_score)

    return [search_manuals]


async def _knowledge_ready(maker: async_sessionmaker) -> bool:
    # 매뉴얼 청크 적재 여부 프로브, 미적재 시 knowledge 도구 미배선 유지
    async with maker() as s:
        count = await s.scalar(select(func.count()).select_from(KnowledgeChunk))
    return bool(count)


async def _live_writer_detected(maker: async_sessionmaker) -> bool:
    # 시드 직전 외부 라이터(연속 시뮬레이터) 활성 여부 프로브 (TEST_HARNESS)
    # 시뮬레이터는 timestamp를 now()로 적재하므로 최신 tick이 현재 시각에 붙어 있으면 활성으로 판단
    # 활성 상태면 시드 대상에도 정상값이 계속 쌓여 최근 창 라인 질의의 격리가 깨지므로 하네스가 차단
    async with maker() as s:
        newest = await s.scalar(select(func.max(EquipmentTelemetry.timestamp)))
    return newest is not None and datetime.now(UTC) - newest < LIVE_WRITER_WINDOW


async def _seed_scenario(maker: async_sessionmaker) -> tuple[list[int], list[str]]:
    # 설비 마스터 보강 + 대상 설비 최근 이상 텔레메트리 주입
    # 삽입 log_id와 이번에 새로 심은 설비 id 반환(정리용), 신규 설비는 teardown에서 제거
    async with maker() as s:
        existing = set(await s.scalars(select(EquipmentMaster.equipment_id)))
        created = [eid for eid, *_ in EQUIPMENT if eid not in existing]
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
        # 알람 발령 이력 1건, get_alarm_history가 원장 기준으로 ERR-402를 집계 (발령 이벤트 1건)
        s.add(
            EquipmentAlarm(
                equipment_id=TARGET,
                metric="temperature",
                alarm_code="ERR-402",
                severity="위험",
                raised_at=base + timedelta(minutes=5),
            )
        )
        await s.commit()
        return [r.log_id for r in rows], created


async def _cleanup(
    maker: async_sessionmaker, log_ids: list[int], created_equipment: list[str]
) -> None:
    # 주입한 텔레메트리 + 이번에 새로 심은 설비 마스터 제거해 DB 오염 방지
    # 신규 설비의 잔여 자식(알람·텔레메트리) 먼저 정리 후 마스터 삭제로 FK 보호
    async with maker() as s:
        await s.execute(delete(EquipmentAlarm).where(EquipmentAlarm.equipment_id == TARGET))
        await s.execute(delete(EquipmentTelemetry).where(EquipmentTelemetry.log_id.in_(log_ids)))
        if created_equipment:
            await s.execute(
                delete(EquipmentAlarm).where(EquipmentAlarm.equipment_id.in_(created_equipment))
            )
            await s.execute(
                delete(EquipmentTelemetry).where(
                    EquipmentTelemetry.equipment_id.in_(created_equipment)
                )
            )
            await s.execute(
                delete(EquipmentMaster).where(EquipmentMaster.equipment_id.in_(created_equipment))
            )
        await s.commit()


@pytest.fixture(params=[False, True], ids=["legacy", "orchestrator"])
async def e2e_runner(request):
    # 두 진단 경로(레거시 Supervisor / 하이브리드 오케스트레이터)를 같은 골든 케이스로 검증 (#139)
    orchestrator_enabled = request.param
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

    # 연속 시뮬레이터가 돌면 시드 대상·경쟁 설비에 텔레메트리가 쌓여 격리가 깨지므로 fail-fast skip
    # E2E는 유한 모드 결정론 주입 전제, 실행 전 simulator 프로세스 정지 필요
    if await _live_writer_detected(maker):
        await engine.dispose()
        pytest.skip(
            "연속 시뮬레이터(라이브 텔레메트리 라이터) 감지, 시드 격리 불가 "
            "(E2E 실행 전 simulator 프로세스 정지 필요)"
        )

    log_ids, created_equipment = await _seed_scenario(maker)
    knowledge_tools = _knowledge_tools(maker) if await _knowledge_ready(maker) else []
    tools = {"realtime": _realtime_tools(maker), "knowledge": knowledge_tools}
    # LLM은 build_agent_graph가 경로별로 지연 생성, 여기선 도구·플래그만 지정
    graph = await build_agent_graph(
        tools_by_server=tools,
        grounding_enabled=False,
        suggestions_enabled=False,
        orchestrator_enabled=orchestrator_enabled,
    )

    async def run(query: str) -> dict:
        state = initial_state(query, [])
        return await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})

    # knowledge 도구 부재 여부·활성 경로를 전달해 조건부 검증에 사용
    yield (
        run,
        {
            "knowledge_available": bool(tools["knowledge"]),
            "orchestrator": orchestrator_enabled,
        },
    )

    await _cleanup(maker, log_ids, created_equipment)
    await engine.dispose()
