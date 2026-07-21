"""장애 대응 계획 서비스 단위 테스트 (NEW_INCIDENT01_PLAN01)"""

from datetime import UTC, datetime

import pytest

from agentory.common.exceptions import NotFoundError
from agentory.modules.incident import service
from agentory.modules.incident.schemas import PlanGeneration
from agentory.modules.worklog.schemas import WorkLogStatus, WorkLogType

OCCURRED_AT = datetime(2026, 7, 13, 1, 30, tzinfo=UTC)
STARTED_AT = datetime(2026, 7, 13, 1, 31, tzinfo=UTC)


def _context(*, incident=True, baseline=True):
    return {
        "notification": {
            "id": 42,
            "occurred_at": OCCURRED_AT,
            "equipment_id": "EQP-A05",
            "alarm_code": "ERR-402",
            "message": "온도 상승과 압력 하강 감지",
        },
        "equipment": {
            "process_type": "Etching",
            "line_name": "A",
            "location": "A-05",
        },
        "incident": (
            {
                "temperature": 65.0,
                "pressure": 97.0,
                "rf_power": 2.0,
                "gas_flow": 100.0,
            }
            if incident
            else None
        ),
        "baseline": (
            [
                {"temperature": 60.0, "pressure": 100.0},
                {"temperature": 62.0, "pressure": 102.0},
            ]
            if baseline
            else []
        ),
        "latest": None,
    }


class _StructuredModel:
    async def ainvoke(self, messages):
        return PlanGeneration(
            summary="냉각 계통 이상 가능성 확인",
            actions=["압력 밸브 상태 확인", "냉각수 유량 확인"],
            safety_checks=["작업 허가 범위 확인"],
        )


class _FakeModel:
    def with_structured_output(self, schema):
        assert schema is PlanGeneration
        return _StructuredModel()


def test_normalize_manual_results_accepts_mcp_text_blocks():
    raw = [
        {
            "type": "text",
            "text": '{"doc_id":"MAN-SOP-001","content":"ERR-402 냉각 조치","score":0.9}',
        }
    ]

    assert service._normalize_manual_results(raw) == [
        {"doc_id": "MAN-SOP-001", "content": "ERR-402 냉각 조치", "score": 0.9}
    ]


def test_citations_deduplicate_document_ids():
    citations = service._citations(
        [
            {"doc_id": "MAN-SOP-001", "content": "첫 청크", "score": 0.9},
            {"doc_id": "MAN-SOP-001", "content": "두 번째 청크", "score": 0.8},
            {"doc_id": "MAN-GDL-001", "content": "가이드", "score": 0.7},
        ]
    )

    assert [item.doc_id for item in citations] == ["MAN-SOP-001", "MAN-GDL-001"]
    assert citations[0].excerpt == "첫 청크"


@pytest.mark.asyncio
async def test_create_plan_returns_work_log_draft(monkeypatch):
    async def fake_context(session, notification_id):
        assert notification_id == 42
        return _context()

    async def fake_search(query, equipment_type):
        assert "ERR-402" in query
        assert "온도 상승" in query
        assert equipment_type == "Etching"
        return [{"doc_id": "MAN-402", "content": "냉각 계통 점검 절차", "score": 0.91}]

    monkeypatch.setattr(service.repository, "fetch_incident_context", fake_context)
    result = await service.create_incident_plan(
        None,
        42,
        llm=_FakeModel(),
        manual_search=fake_search,
        now=STARTED_AT,
    )

    assert result.deviations[0].baseline == 61.0
    assert result.deviations[0].delta == 4.0
    assert result.deviations[1].baseline == 101.0
    assert result.deviations[1].delta == -4.0
    assert result.work_log_draft.work_type == WorkLogType.EMERGENCY
    assert result.work_log_draft.status == WorkLogStatus.IN_PROGRESS
    assert result.work_log_draft.started_at == STARTED_AT
    assert result.work_log_draft.source_notification_id == 42
    assert "[대응 계획]" in result.work_log_draft.plan
    assert "MAN-402" in result.work_log_draft.plan
    assert result.citations[0].doc_id == "MAN-402"
    assert result.warnings == []


@pytest.mark.asyncio
async def test_create_plan_falls_back_without_manual(monkeypatch):
    async def fake_context(session, notification_id):
        return _context()

    async def empty_search(query, equipment_type):
        return []

    monkeypatch.setattr(service.repository, "fetch_incident_context", fake_context)
    result = await service.create_incident_plan(None, 42, manual_search=empty_search)

    assert "라인 정지 없이 압력 밸브 상태 확인" in result.work_log_draft.plan
    assert result.citations == []
    assert any("기본 체크리스트" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_create_plan_warns_when_sensor_context_is_missing(monkeypatch):
    async def fake_context(session, notification_id):
        return _context(incident=False, baseline=False)

    async def empty_search(query, equipment_type):
        return []

    monkeypatch.setattr(service.repository, "fetch_incident_context", fake_context)
    result = await service.create_incident_plan(None, 42, manual_search=empty_search)

    assert result.deviations == []
    assert any("발생 시점 센서 로그" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_create_plan_rejects_missing_notification(monkeypatch):
    async def fake_context(session, notification_id):
        return None

    monkeypatch.setattr(service.repository, "fetch_incident_context", fake_context)
    with pytest.raises(NotFoundError):
        await service.create_incident_plan(None, 999)
