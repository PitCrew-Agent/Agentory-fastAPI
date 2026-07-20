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
1. 핵심 결론(이상 설비·현상)을 1~2문장으로 먼저 제시한다
2. 그 뒤 원인·조치를 필요한 만큼만 짧게 덧붙인다, 서론·배경 설명·인사말은 넣지 않는다
3. 수치 근거는 표 1개로만 제시한다, 표가 불필요하면 생략한다
4. 전체를 6~8줄 이내로 압축한다, 같은 내용을 반복하지 않는다
5. 수집된 근거에 없는 내용을 지어내지 않는다
6. 사용자에게 되묻지 않는다, 질문으로 끝나는 문장·추가 정보 요청·선택지 제시 모두 금지다
7. 근거가 부족하면 확보된 범위에서 확인된 사실을 먼저 서술하고,
   확인하지 못한 부분을 한 문장으로 밝힌다
8. 질의에 설비·기간이 특정되지 않았으면 되묻지 말고 조회된 범위 전체를 기준으로 답한다
9. 인사·잡담·범위 밖 질문에는 표·목록 없이 1~2문장으로만 짧게 답한다
10. 현장에서 바로 읽을 수 있게 간결한 한국어로 작성한다"""

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
