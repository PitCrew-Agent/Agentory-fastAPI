"""POC-A: Supervisor 동적 라우팅 검증 (검증 후 폐기)

성공 기준: 성격이 다른 입력 2개가 각각 다른 워커로 분기 + 응답 5초 이내 + 예외 없음
make_supervisor_node를 직접 호출해 라우팅 결정(next)만 관찰, MCP·워커 실행 불필요
실행: uv run python scripts/poc/poc_a_routing.py
"""

import asyncio
import time

from langchain_core.messages import HumanMessage

from agentory.modules.agent.llm.base import get_chat_model
from agentory.modules.agent.supervisor.router import make_supervisor_node

# 서로 다른 워커로 갈 것으로 기대되는 입력 3종
CASES = [
    ("실시간 데이터 조회", "라인 A 설비의 최근 1시간 온도 로그 보여줘"),
    ("지식 검색", "설비 압력 이상 시 표준 대응 절차가 뭐야?"),
    ("재진단", "방금 진단 결과 근거가 부족한데 다시 진단해줘"),
]


async def main() -> None:
    node = make_supervisor_node(get_chat_model("router"))
    print("[POC-A] router 모델로 라우팅 분기 검증\n")
    print(f"{'케이스':<16} {'라우팅(next)':<16} {'지연(s)':<8} 근거")
    print("-" * 80)
    routed = []
    for label, text in CASES:
        state = {"messages": [HumanMessage(content=text)], "step_count": 0, "entities": {}}
        start = time.perf_counter()
        result = await node(state)
        latency = round(time.perf_counter() - start, 2)
        routed.append(result["next"])
        print(f"{label:<16} {result['next']:<16} {latency:<8} {result['route_reason'][:40]}")

    print()
    distinct = len(set(routed))
    ok = distinct >= 2
    verdict = "성공(동적 라우팅 확인)" if ok else "실패(분기 없음)"
    print(f"분기 종류 수: {distinct} / 입력 {len(CASES)}  ->  {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
