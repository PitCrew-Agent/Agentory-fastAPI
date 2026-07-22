"""Suggest 노드: 후속 추천 질문 생성 (BE_CHAT02_SUGGEST01)

최종 답변 직후 경량 LLM 1회로 후속 질문 3개를 구조화 출력
프론트 퀵 리플라이 칩으로 노출, 실패 시 빈 목록으로 격리해 답변 전달엔 지장 없음
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from agentory.modules.agent.context import ALARM_PATTERN, EQUIPMENT_PATTERN, format_entities
from agentory.modules.agent.prompts.suggest import (
    SUGGEST_CONTEXT_PROMPT,
    SUGGEST_SYSTEM_PROMPT,
)
from agentory.modules.agent.retention import (
    format_data_window,
    get_data_window,
    within_data_window,
)
from agentory.modules.agent.supervisor.state import AgentState

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3

# 이 시스템이 수행 못 하는 실행형 요청(연락·통보·조치 요청·알림 발송 등) 감지 패턴
# 도구가 읽기 전용 진단뿐이라 이런 행동을 시키는 추천은 눌러도 답변 불가, 하드 차단
_UNACTIONABLE_PATTERN = re.compile(
    r"연락|연결|통보|호출|전화|이메일|메일|문자|발송|보내|접수|예약"
    r"|조치\s*요청|조치해|처리\s*요청|처리해|요청해|티켓"
)


class Suggestions(BaseModel):
    # 후속 추천 질문 목록, 정확히 3개 유도
    questions: list[str] = Field(default_factory=list)


def _known_identifiers(entities: dict[str, Any]) -> set[str]:
    # 대화에서 실제 조회된 설비 ID·알람 코드 집합, 추천 범위 검증 기준
    known: set[str] = set()
    for key in ("equipment_ids", "alarm_codes"):
        value = entities.get(key)
        if isinstance(value, list):
            known.update(value)
    return known


def _within_known_scope(question: str, known: set[str]) -> bool:
    # 질문이 언급한 설비 ID·알람 코드가 모두 확인된 범위 안인지 검사
    # 확인 범위 밖 식별자를 언급하면 답변 불가 추천으로 보고 제외
    mentioned = set(EQUIPMENT_PATTERN.findall(question)) | set(ALARM_PATTERN.findall(question))
    return mentioned <= known


def _within_capability(question: str) -> bool:
    # 시스템이 못 하는 실행형 요청(연락·조치 요청·알림 등)이면 False, 능력 밖 추천 제외
    return _UNACTIONABLE_PATTERN.search(question) is None


def make_suggest_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    structured_llm = llm.with_structured_output(Suggestions)

    async def suggest_node(state: AgentState) -> dict[str, Any]:
        # 대화 전체 + 실제 확인된 컨텍스트를 근거로 후속 질문 생성
        entities = state.get("entities") or {}
        known = _known_identifiers(entities)
        equipment_ids = entities.get("equipment_ids") or []
        primary_equipment = equipment_ids[0] if equipment_ids else None
        context_prompt = SUGGEST_CONTEXT_PROMPT.format(known_context=format_entities(entities))
        # 방금 다룬 설비의 실제 보유 구간을 주입해 데이터 없는 기간 제안 억제(설비 미특정 시 전역)
        primary_window = await get_data_window(primary_equipment)
        system_prompt = SUGGEST_SYSTEM_PROMPT.format(
            available_window=format_data_window(primary_window)
        )
        try:
            result = await structured_llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    SystemMessage(content=context_prompt),
                    *state["messages"],
                ]
            )
            # 확인 범위 밖 식별자·능력 밖 실행 요청·조회 불가 기간을 지어낸 질문은 하드 차단
            questions: list[str] = []
            for candidate in (q.strip() for q in result.questions if q.strip()):
                if not _within_known_scope(candidate, known):
                    continue
                if not _within_capability(candidate):
                    continue
                # 질문이 특정 설비를 지목하면 그 설비 보유 구간, 아니면 주 설비 기준으로 기간 검증
                mentioned = EQUIPMENT_PATTERN.findall(candidate)
                window = await get_data_window(mentioned[0]) if mentioned else primary_window
                if not within_data_window(candidate, window):
                    continue
                questions.append(candidate)
                if len(questions) >= MAX_SUGGESTIONS:
                    break
        except Exception as exc:
            # 생성 실패 시 빈 목록으로 격리 (칩만 미표시)
            log.warning("[suggest] 추천 질문 생성 실패: %s", exc)
            questions = []
        return {"suggested_questions": questions}

    return suggest_node
