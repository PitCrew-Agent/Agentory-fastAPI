"""Suggest 노드: 후속 추천 질문 생성 (BE_CHAT02_SUGGEST01)

최종 답변 직후 경량 LLM 1회로 후속 질문 3개를 구조화 출력
프론트 퀵 리플라이 칩으로 노출, 실패 시 빈 목록으로 격리해 답변 전달엔 지장 없음
"""

import logging
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
from agentory.modules.agent.supervisor.state import AgentState

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3


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


def make_suggest_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    structured_llm = llm.with_structured_output(Suggestions)

    async def suggest_node(state: AgentState) -> dict[str, Any]:
        # 대화 전체 + 실제 확인된 컨텍스트를 근거로 후속 질문 생성
        entities = state.get("entities") or {}
        known = _known_identifiers(entities)
        context_prompt = SUGGEST_CONTEXT_PROMPT.format(known_context=format_entities(entities))
        try:
            result = await structured_llm.ainvoke(
                [
                    SystemMessage(content=SUGGEST_SYSTEM_PROMPT),
                    SystemMessage(content=context_prompt),
                    *state["messages"],
                ]
            )
            # 확인 범위 밖 식별자를 지어낸 질문은 후처리에서 하드 차단
            questions = [q.strip() for q in result.questions if q.strip()]
            questions = [q for q in questions if _within_known_scope(q, known)][:MAX_SUGGESTIONS]
        except Exception as exc:
            # 생성 실패 시 빈 목록으로 격리 (칩만 미표시)
            log.warning("[suggest] 추천 질문 생성 실패: %s", exc)
            questions = []
        return {"suggested_questions": questions}

    return suggest_node
