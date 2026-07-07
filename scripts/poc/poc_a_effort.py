"""POC-A 보조: 라우터 reasoning_effort별 지연 비교 (검증 후 폐기)

gpt-5-mini 라우터에서 effort 조정이 지연을 얼마나 줄이는지 측정
실행: uv run python scripts/poc/poc_a_effort.py
"""

import asyncio
import time

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from agentory.core.config import get_settings
from agentory.modules.agent.supervisor.router import make_supervisor_node

settings = get_settings()
CASE = ("지식 검색", "설비 압력 이상 시 표준 대응 절차가 뭐야?")
EFFORTS = [None, "low", "minimal"]


async def run_effort(effort: str | None) -> tuple:
    kwargs = {"model": settings.llm_model, "api_key": settings.openai_api_key}
    if effort:
        kwargs["reasoning_effort"] = effort
    node = make_supervisor_node(ChatOpenAI(**kwargs))
    state = {"messages": [HumanMessage(content=CASE[1])], "step_count": 0, "entities": {}}
    start = time.perf_counter()
    try:
        result = await node(state)
    except Exception as exc:
        return (effort or "default", "ERROR", f"{type(exc).__name__}: {exc}"[:50])
    return (effort or "default", result["next"], round(time.perf_counter() - start, 2))


async def main() -> None:
    print(f"[POC-A effort] 모델 {settings.llm_model}, 케이스: {CASE[0]}\n")
    print(f"{'reasoning_effort':<18} {'라우팅':<16} 지연(s)")
    print("-" * 45)
    for effort in EFFORTS:
        label, routed, latency = await run_effort(effort)
        print(f"{label:<18} {str(routed):<16} {latency}")


if __name__ == "__main__":
    asyncio.run(main())
