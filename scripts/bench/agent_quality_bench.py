"""에이전트 답변 품질·속도 정량 벤치 (AI_AGENT01_REACT01, ADR-0003)

현재 구성(워커 3종·max_steps=6·워커 턴 예산 제외)에서 질의당 지연·LLM 호출수·steps와
답변 품질(라우팅 정확도·정답 키워드 포함·인용 유무)을 실측한다.
품질 판정은 골든 방식(answer_contains·tool_called·citation)의 객관 기준을 사용한다.

실행: AGENT_MAX_STEPS=6 uv run python scripts/bench/agent_quality_bench.py --reps 2
전제: mcp-realtime(:8101)·mcp-knowledge(:8102)·mcp-maintenance(:8103) 기동, DB 시드, OPENAI_API_KEY
"""

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph

# 워크로드: 워커 3종 경로 + 범위밖 잡담, 시드 설비(EQP-A05: ERR-401+WRN-702, 수리 2건) 기반
# (라벨, 질의, 기대 도구 집합, 정답 키워드 후보, 인용 필요)
CASES = [
    (
        "data",
        "EQP-A05 지금 상태 어때? 무슨 알람이 떠 있어?",
        {"get_sensor_logs", "get_alarm_history", "get_equipment_metadata"},
        ["EQP-A05"],
        False,
    ),
    (
        "knowledge",
        "ERR-401 알람 원인이랑 SOP 조치 방법 알려줘",
        {"search_manuals"},
        ["온도", "냉각", "센서"],
        True,
    ),
    (
        "maintenance",
        "EQP-A05 전에도 같은 고장 난 적 있어? 그때 무슨 조치 했는지도 알려줘",
        {"get_repair_history", "get_maintenance_summary"},
        ["ERR-401", "냉각", "수리"],
        False,
    ),
    (
        "chitchat",
        "고마워 수고했어",
        set(),  # 워커 호출 없어야 함
        [],
        False,
    ),
]


class Meter(BaseCallbackHandler):
    def __init__(self) -> None:
        self.calls = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:
        self.calls += 1


def _tools_called(messages) -> list[str]:
    return [
        tc["name"]
        for m in messages
        if isinstance(m, AIMessage) and m.tool_calls
        for tc in m.tool_calls
    ]


def _final_answer(messages) -> str:
    # 도구 호출 없는 마지막 AIMessage(finalizer 답변)
    answers = [m for m in messages if isinstance(m, AIMessage) and m.content and not m.tool_calls]
    return str(answers[-1].content) if answers else ""


async def run_once(graph, case) -> dict:
    label, query, want_tools, keywords, need_cite = case
    meter = Meter()
    state = initial_state(query, history=[], equipment_id=None)
    t0 = time.monotonic()
    final = await graph.ainvoke(
        state, config={"recursion_limit": RECURSION_LIMIT, "callbacks": [meter]}
    )
    dt = time.monotonic() - t0

    tools = set(_tools_called(final["messages"]))
    answer = _final_answer(final["messages"])
    citations = final.get("citations") or []

    # 품질 판정 (객관 기준)
    if want_tools:
        routing_ok = bool(tools & want_tools)
    else:
        routing_ok = len(tools) == 0  # 잡담은 워커 미호출이 정답
    keyword_ok = (not keywords) or any(k in answer for k in keywords)
    citation_ok = (not need_cite) or len(citations) > 0
    passed = routing_ok and keyword_ok and citation_ok

    return {
        "label": label,
        "latency": dt,
        "calls": meter.calls,
        "steps": final.get("step_count", 0),
        "routing_ok": routing_ok,
        "keyword_ok": keyword_ok,
        "citation_ok": citation_ok,
        "passed": passed,
        "tools": sorted(tools),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    args = ap.parse_args()

    graph = await build_agent_graph(suggestions_enabled=False)
    rows: list[dict] = []
    for case in CASES:
        for rep in range(args.reps):
            r = await run_once(graph, case)
            r["rep"] = rep
            rows.append(r)
            print(
                f"[{r['label']}] rep{rep}: {r['latency']:.1f}s calls={r['calls']} "
                f"steps={r['steps']} pass={r['passed']} tools={r['tools']}"
            )

    print("\n===== 케이스별 집계 =====")
    summary = []
    for label, *_ in CASES:
        sub = [r for r in rows if r["label"] == label]
        agg = {
            "case": label,
            "n": len(sub),
            "p50_s": round(statistics.median([r["latency"] for r in sub]), 1),
            "mean_calls": round(statistics.mean([r["calls"] for r in sub]), 1),
            "mean_steps": round(statistics.mean([r["steps"] for r in sub]), 1),
            "pass_rate": round(sum(r["passed"] for r in sub) / len(sub), 2),
        }
        summary.append(agg)
        print(json.dumps(agg, ensure_ascii=False))

    overall = {
        "overall_pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 2),
        "routing_ok_rate": round(sum(r["routing_ok"] for r in rows) / len(rows), 2),
        "keyword_ok_rate": round(sum(r["keyword_ok"] for r in rows) / len(rows), 2),
        "median_latency_s": round(statistics.median([r["latency"] for r in rows]), 1),
    }
    print("\n" + json.dumps(overall, ensure_ascii=False))
    payload = {"rows": rows, "summary": summary, "overall": overall}
    with open("scripts/bench/agent_quality_result.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
