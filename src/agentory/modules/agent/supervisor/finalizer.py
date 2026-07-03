"""Finalizer 노드: 최종 답변 합성 + 출처 인용 (NEW_TRUST01_CITE01)

워커 보고를 종합해 사용자 답변을 생성하고, 수집된 도구 결과에서 근거(doc_id·데이터
기준 시각)를 모아 citations로 부착 (설계 문서 §4.5)
"""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from agentory.modules.agent.supervisor.state import AgentState

FINALIZER_SYSTEM_PROMPT = """\
너는 제조 설비 진단 결과를 현장 엔지니어에게 전달하는 답변 작성자다.
워커들이 수집한 센서 데이터와 매뉴얼 근거를 종합해 최종 답변을 작성한다.

[작성 규칙]
1. 이상 설비·현상(수치 근거)·원인·조치 방법 순으로 명확하게 정리한다
2. 센서 추이가 있으면 마크다운 표로 제시한다
3. 수집된 근거에 없는 내용을 지어내지 않는다, 정보가 부족하면 부족하다고 밝힌다
4. 현장에서 바로 읽을 수 있게 간결한 한국어로 작성한다"""

DOC_ID_PATTERN = re.compile(r"MAN-[A-Z]+-\d+")


def _collect_citations(messages: list) -> list[dict[str, Any]]:
    # 도구 결과(ToolMessage)에서 doc_id와 데이터 기준 시각을 근거로 수집
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = str(msg.content)
        for doc_id in DOC_ID_PATTERN.findall(content):
            if doc_id not in seen:
                seen.add(doc_id)
                citations.append({"doc_id": doc_id})
        # 실시간 도구 결과의 최신 timestamp를 데이터 기준 시각으로 사용
        timestamps = re.findall(r'"timestamp":\s*"([^"]+)"', content)
        if timestamps:
            latest = max(timestamps)
            key = f"telemetry@{latest}"
            if key not in seen:
                seen.add(key)
                citations.append({"doc_id": "telemetry", "data_as_of": latest})
    return citations


def make_finalizer_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def finalizer_node(state: AgentState) -> dict[str, Any]:
        # 수집된 대화 전체를 근거로 최종 답변 합성
        answer = await llm.ainvoke(
            [SystemMessage(content=FINALIZER_SYSTEM_PROMPT), *state["messages"]]
        )
        answer.name = "finalizer"
        citations = _collect_citations(state["messages"])
        return {"messages": [answer], "citations": citations}

    return finalizer_node


def _serialize_context(messages: list) -> str:
    # Grounding 검증에 넘길 수집 컨텍스트(도구 결과) 직렬화
    observations = [str(m.content) for m in messages if isinstance(m, ToolMessage)]
    return json.dumps(observations, ensure_ascii=False)[:6000]


def make_grounding_node(llm: BaseChatModel) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def grounding_node(state: AgentState) -> dict[str, Any]:
        # 최종 답변이 수집 컨텍스트에 근거하는지 LLM 1회로 판정 (NEW_TRUST02_GROUND01)
        final = next(
            (m for m in reversed(state["messages"]) if isinstance(m, AIMessage) and m.content), None
        )
        if final is None:
            return {"grounded": None}
        prompt = (
            "다음 답변이 수집된 컨텍스트에 근거하는지 판정하라. "
            'JSON {"grounded": true|false}만 출력한다.\n\n'
            f"[컨텍스트]\n{_serialize_context(state['messages'])}\n\n"
            f"[답변]\n{final.content}"
        )
        try:
            verdict = await llm.ainvoke([SystemMessage(content=prompt)])
            grounded = "true" in str(verdict.content).lower()
        except Exception:
            # 검증 실패해도 답변은 정상 전달, 배지만 미표시
            grounded = None
        return {"grounded": grounded}

    return grounding_node
