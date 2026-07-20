"""장비 상태 기반 추천 메시지 생성 (NEW_TWIN01_SUGGEST01)

장비 선택 시 현재 상태(상태 등급·알람·센서값)를 근거로 경량 LLM 1회로 추천 메시지 3개를 구조화 출력
대화 이력을 쓰는 후속 추천(BE_CHAT02_SUGGEST01)과 달리 장비 상태만으로 시드
확인 범위(이 설비·알람) 밖 식별자를 지어낸 추천은 하드 필터로 차단
실패 시 빈 목록으로 격리해 장비 상세 응답엔 지장 없음
"""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from agentory.modules.agent.context import ALARM_PATTERN, EQUIPMENT_PATTERN
from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.prompts.equipment_suggest import (
    EQUIPMENT_SUGGEST_CONTEXT_PROMPT,
    EQUIPMENT_SUGGEST_SYSTEM_PROMPT,
)
from agentory.modules.agent.retention import format_available_window, within_available_window

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3

# 프롬프트 상태 블록에 노출할 센서 변수 라벨 (EquipmentDetail 필드명과 동일)
SENSOR_LABELS: dict[str, str] = {
    "temperature": "온도",
    "pressure": "압력",
    "rf_power": "RF 파워",
    "gas_flow": "가스 유량",
}


class EquipmentSuggestions(BaseModel):
    # 장비 상태 기반 추천 메시지 목록, 정확히 3개 유도
    messages: list[str] = Field(default_factory=list)


def _known_identifiers(equipment_id: str, alarm_code: str | None) -> set[str]:
    # 이 설비 ID와 알람 코드만 추천 허용 범위, 그 밖 식별자 언급은 차단 기준
    known = {equipment_id}
    if alarm_code:
        known.add(alarm_code)
    return known


def _within_known_scope(message: str, known: set[str]) -> bool:
    # 메시지가 언급한 설비 ID·알람 코드가 모두 확인된 범위 안인지 검사
    # 확인 범위 밖 식별자를 언급하면 답변 불가 추천으로 보고 제외
    mentioned = set(EQUIPMENT_PATTERN.findall(message)) | set(ALARM_PATTERN.findall(message))
    return mentioned <= known


def _build_context(
    equipment_id: str,
    status: str,
    alarm_code: str | None,
    alarm_metrics: list[str],
    sensors: dict[str, float | None],
) -> str:
    # 선택 설비 현재 상태를 프롬프트 주입용 평문 블록으로 구성
    lines = [f"- 설비: {equipment_id}", f"- 상태: {status}"]
    lines.append(f"- 알람 코드: {alarm_code}" if alarm_code else "- 알람 코드: 없음")
    if alarm_metrics:
        metric_labels = ", ".join(SENSOR_LABELS.get(m, m) for m in alarm_metrics)
        lines.append(f"- 이상 원인 변수: {metric_labels}")
    readings = [
        f"{SENSOR_LABELS[key]}={value}" for key, value in sensors.items() if value is not None
    ]
    if readings:
        lines.append(f"- 최신 센서값: {', '.join(readings)}")
    return "\n".join(lines)


async def generate_equipment_suggestions(
    *,
    equipment_id: str,
    status: str,
    alarm_code: str | None = None,
    alarm_metrics: list[str] | None = None,
    sensors: dict[str, float | None] | None = None,
    llm: BaseChatModel | None = None,
) -> list[str]:
    # 장비 현재 상태를 근거로 추천 메시지 생성, llm 미지정 시 경량 router 모델 사용(테스트는 주입)
    structured_llm = (llm or get_chat_model("router")).with_structured_output(EquipmentSuggestions)
    known = _known_identifiers(equipment_id, alarm_code)
    context_prompt = EQUIPMENT_SUGGEST_CONTEXT_PROMPT.format(
        equipment_context=_build_context(
            equipment_id, status, alarm_code, alarm_metrics or [], sensors or {}
        )
    )
    # 실제 조회 가능한 최근 구간을 주입해 불가능한 기간 제안 억제
    system_prompt = EQUIPMENT_SUGGEST_SYSTEM_PROMPT.format(
        available_window=format_available_window()
    )
    try:
        result = await structured_llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                SystemMessage(content=context_prompt),
            ]
        )
        # 확인 범위 밖 식별자·조회 불가 기간을 지어낸 메시지는 후처리에서 하드 차단
        messages = [m.strip() for m in result.messages if m.strip()]
        messages = [
            m for m in messages if _within_known_scope(m, known) and within_available_window(m)
        ][:MAX_SUGGESTIONS]
    except Exception as exc:
        # 생성 실패 시 빈 목록으로 격리 (칩만 미표시)
        log.warning("[equipment_suggest] 추천 메시지 생성 실패: %s", exc)
        messages = []
    return messages
