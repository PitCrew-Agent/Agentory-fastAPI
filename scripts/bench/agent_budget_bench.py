"""에이전트 실행 예산 정량 벤치 (AI_AGENT01_REACT01, ADR-0003)

max_steps(10 vs 4)·grounding(on/off) 조합별로 질의당 지연·LLM 호출수·토큰을 실측
그래프를 in-process로 빌드해 ainvoke, 콜백으로 호출수·토큰 집계 (DB 미기록)

실행: uv run python scripts/bench/agent_budget_bench.py --reps 2
전제: mcp-realtime(:8101)·mcp-knowledge(:8102) 기동, DB 시드 완료, OPENAI_API_KEY 설정
"""

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from agentory.core.config import get_settings
from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph

# gpt-5-mini 가정 단가(USD/1M 토큰), 실단가 변동 시 조정 후 재계산
RATE_IN = 0.25
RATE_OUT = 2.00

# 워크로드: 실제 시드 설비·알람 기반, 라우팅 경로가 다른 3종
QUERIES = [
    ("diag_chain", "A라인에서 최근 이상 징후가 있는 설비를 찾아 원인과 조치를 알려줘"),
    ("knowledge", "ERR-402 알람의 원인과 조치 방법을 알려줘"),
    ("diag_one", "EQP-A05 설비에 무슨 문제가 있는지 진단하고 조치를 알려줘"),
]

# (라벨, max_steps, grounding)
CONFIGS = [
    ("A_before(steps10_ground)", 10, True),
    ("B_steps4_ground", 4, True),
    ("C_after(steps4_noground)", 4, False),
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
        # 폴백: 제너레이션 메시지의 usage_metadata 합산
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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2, help="질의·설정당 반복 횟수")
    args = ap.parse_args()

    # grounding 값별 그래프 1회 빌드(빌드 시점 결정), suggestions는 교란 배제 위해 off 고정
    graphs = {}
    for ground in (True, False):
        graphs[ground] = await build_agent_graph(
            grounding_enabled=ground, suggestions_enabled=False
        )

    settings = get_settings()
    rows: list[dict] = []
    for label, steps, ground in CONFIGS:
        settings.agent_max_steps = steps  # 라우터가 런타임에 읽음
        graph = graphs[ground]
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

    # 설정별 집계
    print("\n===== 설정별 집계 =====")
    summary = []
    for label, _, _ in CONFIGS:
        sub = [r for r in rows if r["config"] == label]
        lat = [r["latency"] for r in sub]
        calls = [r["calls"] for r in sub]
        tot_tok = [r["in_tok"] + r["out_tok"] for r in sub]
        out_tok = [r["out_tok"] for r in sub]
        lat_sorted = sorted(lat)
        p50 = statistics.median(lat)
        p95 = lat_sorted[min(len(lat_sorted) - 1, int(round(0.95 * (len(lat_sorted) - 1))))]
        cost = statistics.mean(
            [(r["in_tok"] * RATE_IN + r["out_tok"] * RATE_OUT) / 1_000_000 for r in sub]
        )
        agg = {
            "config": label,
            "n": len(sub),
            "p50_s": round(p50, 2),
            "p95_s": round(p95, 2),
            "mean_s": round(statistics.mean(lat), 2),
            "mean_calls": round(statistics.mean(calls), 1),
            "mean_total_tok": round(statistics.mean(tot_tok)),
            "mean_out_tok": round(statistics.mean(out_tok)),
            "mean_cost_usd": round(cost, 5),
        }
        summary.append(agg)
        print(json.dumps(agg, ensure_ascii=False))

    with open("scripts/bench/agent_budget_result.json", "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "summary": summary}, f, ensure_ascii=False, indent=2)
    print("\n결과 저장: scripts/bench/agent_budget_result.json")


if __name__ == "__main__":
    asyncio.run(main())
