"""에이전트 답변 품질·속도 정량 벤치 (AI_AGENT01_REACT01, ADR-0003)

Supervisor+워커(orchestrator off)와 하이브리드 오케스트레이터(orchestrator on)를 같은 질의로 비교,
설정별 질의당 지연·LLM 호출수·토큰·비용·steps와
답변 품질(라우팅 정확도·정답 키워드 포함·인용 유무)을 실측한다.
품질 판정은 골든 방식(answer_contains·tool_called·citation)의 객관 기준을 사용한다.

실행: AGENT_MAX_STEPS=6 uv run python scripts/bench/agent_quality_bench.py --reps 3
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

# 워크로드: 워커 3종 경로 + 범위밖 잡담, 시드 설비(EQP-A05: 복합 냉각 고장 ERR-402, 수리 2건) 기반
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
        ["ERR-402", "냉각", "수리"],
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


# gpt-5-mini 가정 단가(USD/1M 토큰), 실단가 변동 시 조정 후 재계산
RATE_IN = 0.25
RATE_OUT = 2.00

# (라벨, orchestrator_enabled), 아키텍처 전환 전후를 같은 품질 기준으로 비교
CONFIGS = [
    ("supervisor_before", False),
    ("orchestrator_after", True),
]


class Meter(BaseCallbackHandler):
    # LLM 호출수·입출력 토큰 집계
    def __init__(self) -> None:
        self.calls = 0
        self.in_tok = 0
        self.out_tok = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:
        self.calls += 1
        usage = (response.llm_output or {}).get("token_usage") if response.llm_output else None
        if usage:
            self.in_tok += usage.get("prompt_tokens", 0)
            self.out_tok += usage.get("completion_tokens", 0)
            return
        for gen_list in response.generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg else None
                if um:
                    self.in_tok += um.get("input_tokens", 0)
                    self.out_tok += um.get("output_tokens", 0)


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
        "in_tok": meter.in_tok,
        "out_tok": meter.out_tok,
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

    rows: list[dict] = []
    for cfg_label, orch in CONFIGS:
        graph = await build_agent_graph(suggestions_enabled=False, orchestrator_enabled=orch)
        for case in CASES:
            for rep in range(args.reps):
                r = await run_once(graph, case)
                r["rep"] = rep
                r["config"] = cfg_label
                rows.append(r)
                print(
                    f"[{cfg_label}] {r['label']} rep{rep}: {r['latency']:.1f}s "
                    f"calls={r['calls']} tok={r['in_tok']}/{r['out_tok']} "
                    f"steps={r['steps']} pass={r['passed']} tools={r['tools']}"
                )

    def _cost(sub: list[dict]) -> float:
        return statistics.mean(
            [(r["in_tok"] * RATE_IN + r["out_tok"] * RATE_OUT) / 1_000_000 for r in sub]
        )

    print("\n===== 설정·케이스별 집계 =====")
    summary = []
    for cfg_label, _ in CONFIGS:
        for label, *_ in CASES:
            sub = [r for r in rows if r["config"] == cfg_label and r["label"] == label]
            agg = {
                "config": cfg_label,
                "case": label,
                "n": len(sub),
                "p50_s": round(statistics.median([r["latency"] for r in sub]), 1),
                "mean_calls": round(statistics.mean([r["calls"] for r in sub]), 1),
                "mean_steps": round(statistics.mean([r["steps"] for r in sub]), 1),
                "mean_in_tok": round(statistics.mean([r["in_tok"] for r in sub])),
                "mean_out_tok": round(statistics.mean([r["out_tok"] for r in sub])),
                "mean_cost_usd": round(_cost(sub), 5),
                "pass_rate": round(sum(r["passed"] for r in sub) / len(sub), 2),
            }
            summary.append(agg)
            print(json.dumps(agg, ensure_ascii=False))

    print("\n===== 설정별 종합 =====")
    overall = []
    for cfg_label, _ in CONFIGS:
        sub = [r for r in rows if r["config"] == cfg_label]
        agg = {
            "config": cfg_label,
            "n": len(sub),
            "overall_pass_rate": round(sum(r["passed"] for r in sub) / len(sub), 2),
            "routing_ok_rate": round(sum(r["routing_ok"] for r in sub) / len(sub), 2),
            "keyword_ok_rate": round(sum(r["keyword_ok"] for r in sub) / len(sub), 2),
            "citation_ok_rate": round(sum(r["citation_ok"] for r in sub) / len(sub), 2),
            "median_latency_s": round(statistics.median([r["latency"] for r in sub]), 1),
            "mean_cost_usd": round(_cost(sub), 5),
        }
        overall.append(agg)
        print(json.dumps(agg, ensure_ascii=False))

    payload = {
        "rate_in_usd_per_1m": RATE_IN,
        "rate_out_usd_per_1m": RATE_OUT,
        "reps": args.reps,
        "rows": rows,
        "summary": summary,
        "overall": overall,
    }
    with open("scripts/bench/agent_quality_result.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
