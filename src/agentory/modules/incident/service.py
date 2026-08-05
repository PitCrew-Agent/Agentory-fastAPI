"""장애 대응 계획 생성 서비스 (NEW_INCIDENT01_PLAN01)"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from statistics import median
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import NotFoundError
from agentory.core.config import get_settings
from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.mcp_client.client import load_tools_for_server
from agentory.modules.incident import repository
from agentory.modules.incident.schemas import (
    IncidentPlanResponse,
    ManualCitation,
    PlanGeneration,
    SensorDeviation,
)
from agentory.modules.telemetry.checklists import alarm_metrics, build_checklist_items
from agentory.modules.worklog.schemas import WorkLogCreate, WorkLogStatus, WorkLogType

log = logging.getLogger(__name__)

ManualSearch = Callable[[str, str | None], Awaitable[list[dict[str, Any]]]]

METRIC_INFO = {
    "temperature": ("온도", "°C"),
    "pressure": ("압력", "mTorr"),
    "rf_power": ("RF 파워", "kW"),
    "gas_flow": ("가스 유량", "sccm"),
}


def _work_type(alarm_code: str) -> WorkLogType:
    if alarm_code.startswith("ERR-"):
        return WorkLogType.EMERGENCY
    if alarm_code in {"WRN-701", "WRN-702", "WRN-703", "WRN-704"}:
        return WorkLogType.PREVENTIVE
    if alarm_code.startswith("WRN-"):
        return WorkLogType.REPAIR
    return WorkLogType.ETC


def _deviations(context: dict[str, Any]) -> list[SensorDeviation]:
    incident = context.get("incident")
    if not incident:
        return []
    alarm_code = context["notification"]["alarm_code"]
    metrics = alarm_metrics(alarm_code) or list(METRIC_INFO)
    rows = [row for row in context.get("baseline", []) if row]
    result: list[SensorDeviation] = []
    for metric in metrics:
        incident_value = incident.get(metric)
        if incident_value is None:
            continue
        values = [row[metric] for row in rows if row.get(metric) is not None]
        baseline = float(median(values)) if values else None
        delta = round(incident_value - baseline, 3) if baseline is not None else None
        label, unit = METRIC_INFO[metric]
        result.append(
            SensorDeviation(
                metric=metric,
                label=label,
                unit=unit,
                baseline=round(baseline, 3) if baseline is not None else None,
                incident=round(incident_value, 3),
                delta=delta,
            )
        )
    return result


def _normalize_manual_results(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict):
        if raw.get("doc_id") and raw.get("content"):
            return [raw]
        raw = raw.get("results") or raw.get("result") or raw.get("data") or []
    if not isinstance(raw, list):
        return []
    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            results.extend(_normalize_manual_results(item["text"]))
            continue
        if item.get("doc_id") and item.get("content"):
            results.append(item)
    return results


async def _search_manuals(query: str, equipment_type: str | None) -> list[dict[str, Any]]:
    tools = await load_tools_for_server("knowledge")
    tool = next((item for item in tools if item.name == "search_manuals"), None)
    if tool is None:
        return []
    raw = await tool.ainvoke(
        {
            "query": query,
            "top_k": get_settings().rag_search_top_k,
            "equipment_type": equipment_type,
        }
    )
    results = _normalize_manual_results(raw)
    if results or equipment_type is None:
        return results
    raw = await tool.ainvoke(
        {
            "query": query,
            "top_k": get_settings().rag_search_top_k,
            "equipment_type": None,
        }
    )
    return _normalize_manual_results(raw)


def _manual_query(context: dict[str, Any], deviations: list[SensorDeviation]) -> str:
    notification = context["notification"]
    process_type = context["equipment"].get("process_type") or "제조 설비"
    changes = []
    for item in deviations:
        if item.delta is None:
            changes.append(f"{item.label} 기준 비교 필요")
        elif item.delta > 0:
            changes.append(f"{item.label} 상승")
        elif item.delta < 0:
            changes.append(f"{item.label} 하강")
        else:
            changes.append(f"{item.label} 변화 없음")
    change_text = " ".join(changes) or notification["message"]
    return f"{process_type} {notification['alarm_code']} {change_text} 안전 조치 대응 절차"


def _citations(manuals: list[dict[str, Any]]) -> list[ManualCitation]:
    citations = []
    seen_doc_ids = set()
    for item in manuals:
        doc_id = str(item["doc_id"])
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        citations.append(
            ManualCitation(
                doc_id=doc_id,
                score=float(item["score"]) if item.get("score") is not None else None,
                excerpt=str(item["content"]).strip()[:240],
            )
        )
    return citations


async def _generate_plan(
    context: dict[str, Any],
    deviations: list[SensorDeviation],
    manuals: list[dict[str, Any]],
    llm: BaseChatModel | None,
) -> tuple[PlanGeneration, list[str]]:
    alarm_code = context["notification"]["alarm_code"]
    checklist = build_checklist_items(alarm_code)
    fallback = PlanGeneration(
        summary=f"{context['notification']['equipment_id']} {alarm_code} 대응 점검",
        actions=checklist or ["현장 설비 상태와 알림 발생 조건 확인"],
        safety_checks=["작업 허가 범위와 설비 안전 상태 확인"],
    )
    if not manuals:
        return fallback, ["관련 매뉴얼 검색 결과가 없어 기본 체크리스트로 초안을 생성했습니다"]
    if llm is None and not get_settings().openai_api_key:
        return fallback, ["LLM 설정이 없어 기본 체크리스트로 초안을 생성했습니다"]

    model = llm or get_chat_model("worker")
    grounded_input = {
        "notification": context["notification"],
        "equipment": context["equipment"],
        "deviations": [item.model_dump() for item in deviations],
        "checklist": checklist,
        "manuals": [
            {"doc_id": item["doc_id"], "content": str(item["content"])[:2000]} for item in manuals
        ],
    }
    system = """제조 설비 장애 대응 계획 Agent다
제공된 체크리스트와 매뉴얼 근거 안에서만 현장 작업자가 실행할 조치를 작성한다
설비 제어를 실행했다고 표현하지 않는다
근거에 없는 부품 교체값이나 임계값을 만들지 않는다
actions는 실행 순서대로 짧고 구체적으로 작성한다
safety_checks는 작업 전 확인할 안전 항목만 작성한다"""
    try:
        structured = model.with_structured_output(PlanGeneration)
        raw = await structured.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=json.dumps(grounded_input, ensure_ascii=False, default=str)),
            ]
        )
        plan = raw if isinstance(raw, PlanGeneration) else PlanGeneration.model_validate(raw)
        return plan, []
    except Exception as exc:
        log.warning("장애 대응 계획 LLM 생성 실패: %s", exc)
        return fallback, ["AI 계획 생성에 실패해 기본 체크리스트로 초안을 생성했습니다"]


def _format_value(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _work_log_content(
    context: dict[str, Any],
    deviations: list[SensorDeviation],
    plan: PlanGeneration,
    citations: list[ManualCitation],
) -> str:
    notification = context["notification"]
    lines = [
        "[알림 정보]",
        f"설비: {notification['equipment_id']}",
        f"알람: {notification['alarm_code']}",
        f"발생 시각: {notification['occurred_at'].isoformat()}",
        f"알림 내용: {notification['message']}",
        "",
        "[이상 변화]",
    ]
    if deviations:
        for item in deviations:
            if item.baseline is None or item.delta is None:
                incident_text = _format_value(item.incident)
                lines.append(f"{item.label}: 발생 {incident_text} {item.unit}, 기준 부족")
                continue
            sign = "+" if item.delta > 0 else ""
            lines.append(
                f"{item.label}: 기준 {_format_value(item.baseline)} → 발생 "
                f"{_format_value(item.incident)} {item.unit} "
                f"변화 {sign}{_format_value(item.delta)}"
            )
    else:
        lines.append("발생 시점 센서 로그 확인 필요")
    lines.extend(["", "[판단 요약]", plan.summary, "", "[대응 계획]"])
    lines.extend(f"{index}. {action}" for index, action in enumerate(plan.actions, start=1))
    if plan.safety_checks:
        lines.extend(["", "[안전 확인]"])
        lines.extend(f"- {item}" for item in plan.safety_checks)
    lines.extend(["", "[매뉴얼 근거]"])
    if citations:
        lines.extend(f"- {item.doc_id}" for item in citations)
    else:
        lines.append("검색 근거 없음")
    return "\n".join(lines)


async def create_incident_plan(
    session: AsyncSession,
    notification_id: int,
    *,
    llm: BaseChatModel | None = None,
    manual_search: ManualSearch | None = None,
    now: datetime | None = None,
) -> IncidentPlanResponse:
    context = await repository.fetch_incident_context(session, notification_id)
    if context is None:
        raise NotFoundError(
            "error.notification.not_found",
            params={"id": notification_id},
        )

    deviations = _deviations(context)
    warnings = []
    if context["incident"] is None:
        warnings.append("발생 시점 센서 로그를 찾지 못했습니다")
    elif not context["baseline"]:
        warnings.append("발생 전 정상 센서 로그가 없어 기준값을 계산하지 못했습니다")

    search = manual_search or _search_manuals
    query = _manual_query(context, deviations)
    try:
        async with asyncio.timeout(get_settings().incident_manual_search_timeout_seconds):
            manuals = await search(query, context["equipment"].get("process_type"))
    except TimeoutError:
        log.warning("장애 대응 매뉴얼 검색 시간 초과")
        manuals = []
        warnings.append("매뉴얼 검색 응답이 지연되어 기본 체크리스트로 전환했습니다")
    except Exception as exc:
        log.warning("장애 대응 매뉴얼 검색 실패: %s", exc)
        manuals = []
        warnings.append("매뉴얼 검색 서버에 연결하지 못했습니다")

    plan, plan_warnings = await _generate_plan(context, deviations, manuals, llm)
    warnings.extend(plan_warnings)
    citations = _citations(manuals)
    notification = context["notification"]
    draft = WorkLogCreate(
        work_type=_work_type(notification["alarm_code"]),
        started_at=now or datetime.now(UTC),
        ended_at=None,
        plan=_work_log_content(context, deviations, plan, citations),
        status=WorkLogStatus.IN_PROGRESS,
        source_notification_id=notification_id,
    )
    return IncidentPlanResponse(
        notification_id=notification_id,
        equipment_id=notification["equipment_id"],
        alarm_code=notification["alarm_code"],
        occurred_at=notification["occurred_at"],
        summary=plan.summary,
        deviations=deviations,
        work_log_draft=draft,
        citations=citations,
        warnings=warnings,
    )
