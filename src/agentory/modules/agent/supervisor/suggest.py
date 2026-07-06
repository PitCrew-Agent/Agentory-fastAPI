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

from agentory.modules.agent.prompts.suggest import SUGGEST_SYSTEM_PROMPT
from agentory.modules.agent.supervisor.state import AgentState

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3


class Suggestions(BaseModel):
    # 후속 추천 질문 목록, 정확히 3개 유도
    questions: list[str] = Field(default_factory=list)


def make_suggest_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    structured_llm = llm.with_structured_output(Suggestions)

    async def suggest_node(state: AgentState) -> dict[str, Any]:
        # 대화 전체를 근거로 후속 질문 생성
        try:
            result = await structured_llm.ainvoke(
                [SystemMessage(content=SUGGEST_SYSTEM_PROMPT), *state["messages"]]
            )
            questions = [q.strip() for q in result.questions if q.strip()][:MAX_SUGGESTIONS]
        except Exception as exc:
            # 생성 실패 시 빈 목록으로 격리 (칩만 미표시)
            log.warning("[suggest] 추천 질문 생성 실패: %s", exc)
            questions = []
        return {"suggested_questions": questions}

    return suggest_node
