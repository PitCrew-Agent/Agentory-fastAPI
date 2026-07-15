"""에이전트 아키텍처 A/B 정량 벤치 (AI_AGENT01_REACT01, #139)

기존 Supervisor+워커(orchestrator off) vs 하이브리드 Planner+병렬 Fetch(orchestrator on)를
같은 워크로드로 비교, 질의당 지연·LLM 호출수·토큰·비용을 실측

모든 MCP 도구를 결정적 스텁으로 대체하고 모의 지연을 주입해, RAG 품질·DB 시드와 무관하게
순수 아키텍처 차이(LLM 호출수·토큰·Fetch 병렬성)만 측정한다 (A축 분리)

실행: uv run python scripts/bench/agent_arch_bench.py --reps 3 --tool-latency 0.3
전제: OPENAI_API_KEY 설정 (MCP 서버·DB 불필요, 도구는 스텁)
"""

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph

# gpt-5-mini 가정 단가(USD/1M 토큰), 실단가 변동 시 조정 후 재계산
RATE_IN = 0.25
RATE_OUT = 2.00

# 도구별 모의 지연(초), MCP 왕복을 흉내내 병렬 Fetch 이득이 드러나게 함, main에서 갱신
TOOL_LATENCY = 0.3

# 워크로드: 라우팅 경로가 다른 3종 (기존 예산 벤치와 동일 질의)
QUERIES = [
    ("diag_chain", "A라인에서 최근 이상 징후가 있는 설비를 찾아 원인과 조치를 알려줘"),
    ("knowledge", "ERR-402 알람의 원인과 조치 방법을 알려줘"),
    ("diag_one", "EQP-A05 설비에 무슨 문제가 있는지 진단하고 조치를 알려줘"),
]

# (라벨, orchestrator_enabled)
CONFIGS = [
    ("supervisor_before", False),
    ("orchestrator_after", True),
]


async def _delay() -> None:
    # MCP 왕복 모의 지연, 병렬 실행이면 gather로 겹쳐 총 지연이 줄어듦
    await asyncio.sleep(TOOL_LATENCY)


@tool
async def get_sensor_logs(
    line_name: str = "", equipment_id: str = "", start: str = "", end: str = ""
) -> str:
    """라인·설비의 실시간 센서 로그 조회"""
    await _delay()
    return "EQP-003 temperature 65.0 (평소 40) alarm ERR-402 x3 line=A라인"


@tool
async def get_alarm_history(
    equipment_id: str = "", line_name: str = "", start: str = "", end: str = ""
) -> str:
    """설비 알람 이력 조회"""
    await _delay()
    return "EQP-003 ERR-402 3건 최근 1시간, 간헐 발생"


@tool
async def get_equipment_metadata(equipment_id: str = "", line_name: str = "") -> str:
    """설비 메타데이터 조회"""
    await _delay()
    return "EQP-003 에칭 장비 line=A라인 정상범위 온도<=60"


@tool
async def get_repair_history(equipment_id: str = "", start: str = "", end: str = "") -> str:
    """설비 과거 수리 이력 조회"""
    await _delay()
    return "EQP-A05 repair 2026-07-02 ERR-401 냉각수 라인 세정, 반복 고장 2회"


@tool
async def get_maintenance_summary(equipment_id: str = "") -> str:
    """설비 정비 요약 조회"""
    await _delay()
    return "EQP-A05 최근 90일 정비 3회, 냉각 계통 반복"


@tool
async def search_manuals(query: str = "", top_k: int = 3) -> str:
    """장애 조치 매뉴얼 검색"""
    await _delay()
    return "MAN-ETC-042 ERR-402는 냉각수 밸브 압력 저하 시 발생, 60도 이상이면 밸브 15% 개방"


def _stub_tools_by_server() -> dict[str, list]:
    # 서버별 스텁 도구, 실제 도구명·서버 매핑과 동일하게 구성
    return {
        "realtime": [get_sensor_logs, get_alarm_history, get_equipment_metadata],
        "maintenance": [get_repair_history, get_maintenance_summary],
        "knowledge": [search_manuals],
    }


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


async def run_once(graph, query: str) -> dict:
    meter = Meter()
    state = initial_state(query, history=[], equipment_id=None)
    t0 = time.monotonic()
    final = await graph.ainvoke(
        state, config={"recursion_limit": RECURSION_LIMIT, "callbacks": [meter]}
    )
    dt = time.monotonic() - t0
    return {
        "latency": dt,
        "calls": meter.calls,
        "in_tok": meter.in_tok,
        "out_tok": meter.out_tok,
        "steps": final.get("step_count", 0),
    }


def _aggregate(rows: list[dict], label: str) -> dict:
    sub = [r for r in rows if r["config"] == label]
    lat = [r["latency"] for r in sub]
    cost = statistics.mean(
        [(r["in_tok"] * RATE_IN + r["out_tok"] * RATE_OUT) / 1_000_000 for r in sub]
    )
    return {
        "config": label,
        "n": len(sub),
        "mean_s": round(statistics.mean(lat), 2),
        "p50_s": round(statistics.median(lat), 2),
        "mean_calls": round(statistics.mean([r["calls"] for r in sub]), 1),
        "mean_in_tok": round(statistics.mean([r["in_tok"] for r in sub])),
        "mean_out_tok": round(statistics.mean([r["out_tok"] for r in sub])),
        "mean_cost_usd": round(cost, 5),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="질의·설정당 반복 횟수")
    ap.add_argument("--tool-latency", type=float, default=0.3, help="도구 모의 지연(초)")
    args = ap.parse_args()

    global TOOL_LATENCY
    TOOL_LATENCY = args.tool_latency

    tools = _stub_tools_by_server()
    # 아키텍처별 그래프 1회 빌드, grounding·suggestions는 교란 배제 위해 off 고정
    graphs = {}
    for label, orch in CONFIGS:
        graphs[label] = await build_agent_graph(
            tools_by_server=tools,
            grounding_enabled=False,
            suggestions_enabled=False,
            orchestrator_enabled=orch,
        )

    rows: list[dict] = []
    for label, _ in CONFIGS:
        graph = graphs[label]
        for qname, qtext in QUERIES:
            for rep in range(args.reps):
                r = await run_once(graph, qtext)
                r.update(config=label, query=qname, rep=rep)
                rows.append(r)
                print(
                    f"[{label}] {qname} rep{rep}: "
                    f"{r['latency']:.2f}s calls={r['calls']} "
                    f"tok={r['in_tok']}/{r['out_tok']} steps={r['steps']}"
                )

    print("\n===== 아키텍처별 집계 =====")
    summary = [_aggregate(rows, label) for label, _ in CONFIGS]
    for agg in summary:
        print(json.dumps(agg, ensure_ascii=False))

    # before 대비 after 개선율
    before = next((s for s in summary if s["config"] == "supervisor_before"), None)
    after = next((s for s in summary if s["config"] == "orchestrator_after"), None)
    if before and after:

        def pct(b, a):
            return round((b - a) / b * 100, 1) if b else 0.0

        delta = {
            "latency_reduction_pct": pct(before["mean_s"], after["mean_s"]),
            "calls_reduction_pct": pct(before["mean_calls"], after["mean_calls"]),
            "in_tok_reduction_pct": pct(before["mean_in_tok"], after["mean_in_tok"]),
            "cost_reduction_pct": pct(before["mean_cost_usd"], after["mean_cost_usd"]),
        }
        print("\n===== 개선율 (before 대비 after) =====")
        print(json.dumps(delta, ensure_ascii=False))
        summary.append({"config": "_delta", **delta})

    with open("scripts/bench/agent_arch_result.json", "w", encoding="utf-8") as f:
        json.dump(
            {"tool_latency": TOOL_LATENCY, "reps": args.reps, "rows": rows, "summary": summary},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print("\n결과 저장: scripts/bench/agent_arch_result.json")


if __name__ == "__main__":
    asyncio.run(main())
