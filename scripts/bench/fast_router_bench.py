"""Fast Router 바이패스 정량 벤치 (AI_AGENT01_REACT01, #139)

Fast Router(규칙 우선 분류 + Direct Answer 바이패스) on/off를 같은 질의로 비교,
질의당 지연·LLM 호출수·토큰·비용을 실측한다.
아키텍처 A/B(agent_arch_bench.py)는 orchestrator_enabled 축이라 Fast Router가 양쪽 모두
켜진 채로 측정되어 바이패스 효과가 분리되지 않는다. 본 벤치가 그 축을 분리한다.

1부는 LLM 호출 없이 classify_intent 분류 정확도만 채점한다(비용 0).
direct 오탐은 데이터 수집 누락으로 직결되므로 diagnostic 재현율을 별도로 본다.
2부는 실제 그래프를 돌려 바이패스 이득을 측정한다.

모든 MCP 도구를 결정적 스텁으로 대체해 RAG 품질·DB 시드와 무관하게 분류·경로 차이만 측정한다.

실행: uv run python scripts/bench/fast_router_bench.py --reps 3
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
from agentory.modules.agent.supervisor.fast_router import DIAGNOSTIC, DIRECT, classify_intent
from agentory.modules.agent.supervisor.graph import build_agent_graph

# gpt-5-mini 가정 단가(USD/1M 토큰), 실단가 변동 시 조정 후 재계산
RATE_IN = 0.25
RATE_OUT = 2.00

# 도구별 모의 지연(초), main에서 갱신
TOOL_LATENCY = 0.3

# 분류 채점용 라벨 세트, direct는 바이패스 대상 / diagnostic은 데이터 수집 필요
# (질의, 기대 분류)
LABELED = [
    ("안녕? 반가워", DIRECT),
    ("고마워 수고했어", DIRECT),
    ("hi", DIRECT),
    ("thanks", DIRECT),
    ("잘 부탁드립니다", DIRECT),
    ("또 봐", DIRECT),
    ("EQP-A05 상태 알려줘", DIAGNOSTIC),
    ("ERR-402 원인이 뭐야", DIAGNOSTIC),
    ("A라인 온도 이상한 설비 있어?", DIAGNOSTIC),
    ("최근 정비 이력 보여줘", DIAGNOSTIC),
    ("압력이 계속 불안정한데 왜 그래?", DIAGNOSTIC),
    ("매뉴얼에서 조치 방법 찾아줘", DIAGNOSTIC),
    ("안녕? EQP-A05 상태도 같이 알려줘", DIAGNOSTIC),  # 혼합, 진단 신호 우선이 정답
    ("오늘 날씨 어때?", DIAGNOSTIC),  # 범위 밖이나 잡담 신호 없음, 보수적으로 diagnostic
    # 적대적 케이스: 인사·감사 표현이 붙었으나 실제로는 데이터 수집이 필요한 질의
    # 진단 키워드가 없어 규칙이 direct로 흘릴 수 있는 지점 (치명 오탐 후보)
    ("고마워, 근데 아까 그 값 다시 보여줄래?", DIAGNOSTIC),
    ("감사합니다. 어제 것도 뽑아주세요", DIAGNOSTIC),
    ("수고했어, 방금 그거 그래프로 그려줘", DIAGNOSTIC),
    ("안녕, 그 다음 건 어떻게 됐어?", DIAGNOSTIC),
]

# 바이패스 이득 측정용 질의, 잡담 2종 + 회귀 가드용 진단 1종
QUERIES = [
    ("smalltalk_greeting", "안녕? 반가워"),
    ("smalltalk_thanks", "고마워 수고했어"),
    ("diag_guard", "EQP-A05 설비에 무슨 문제가 있는지 진단하고 조치를 알려줘"),
]

# (라벨, fast_router_enabled)
CONFIGS = [
    ("fast_router_off", False),
    ("fast_router_on", True),
]


async def _delay() -> None:
    await asyncio.sleep(TOOL_LATENCY)


@tool
async def get_sensor_logs(
    line_name: str = "", equipment_id: str = "", start: str = "", end: str = ""
) -> str:
    """라인·설비의 실시간 센서 로그 조회"""
    await _delay()
    return "EQP-A05 temperature 65.0 (평소 40) alarm ERR-402 x3 line=A라인"


@tool
async def get_alarm_history(
    equipment_id: str = "", line_name: str = "", start: str = "", end: str = ""
) -> str:
    """설비 알람 이력 조회"""
    await _delay()
    return "EQP-A05 ERR-402 3건 최근 1시간, 간헐 발생"


@tool
async def get_equipment_metadata(equipment_id: str = "", line_name: str = "") -> str:
    """설비 메타데이터 조회"""
    await _delay()
    return "EQP-A05 에칭 장비 line=A라인 정상범위 온도<=60"


@tool
async def get_repair_history(equipment_id: str = "", start: str = "", end: str = "") -> str:
    """설비 과거 수리 이력 조회"""
    await _delay()
    return "EQP-A05 repair 2026-07-02 ERR-402 냉각 계통 밸브 조정, 반복 고장 2회"


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


def score_classification() -> dict:
    # LLM 호출 없이 규칙 분류만 채점, direct 오탐(데이터 누락)과 diagnostic 재현율 분리 집계
    hits = [(q, want, classify_intent(q)) for q, want in LABELED]
    correct = [h for h in hits if h[1] == h[2]]
    direct_want = [h for h in hits if h[1] == DIRECT]
    diag_want = [h for h in hits if h[1] == DIAGNOSTIC]
    # diagnostic을 direct로 잘못 보내면 데이터 수집이 통째로 생략됨 (치명 오탐)
    fatal = [h for h in hits if h[1] == DIAGNOSTIC and h[2] == DIRECT]
    return {
        "n": len(hits),
        "accuracy": round(len(correct) / len(hits), 3),
        "direct_recall": round(sum(1 for h in direct_want if h[2] == DIRECT) / len(direct_want), 3),
        "diagnostic_recall": round(
            sum(1 for h in diag_want if h[2] == DIAGNOSTIC) / len(diag_want), 3
        ),
        "fatal_misroute": len(fatal),
        "misroutes": [{"query": q, "want": w, "got": g} for q, w, g in hits if w != g],
    }


async def run_once(graph, query: str) -> dict:
    meter = Meter()
    state = initial_state(query, history=[], equipment_id=None)
    t0 = time.monotonic()
    await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT, "callbacks": [meter]})
    dt = time.monotonic() - t0
    return {
        "latency": dt,
        "calls": meter.calls,
        "in_tok": meter.in_tok,
        "out_tok": meter.out_tok,
    }


def _aggregate(rows: list[dict], label: str, query: str | None = None) -> dict:
    sub = [r for r in rows if r["config"] == label and (query is None or r["query"] == query)]
    lat = [r["latency"] for r in sub]
    cost = statistics.mean(
        [(r["in_tok"] * RATE_IN + r["out_tok"] * RATE_OUT) / 1_000_000 for r in sub]
    )
    agg = {
        "config": label,
        "n": len(sub),
        "mean_s": round(statistics.mean(lat), 2),
        "p50_s": round(statistics.median(lat), 2),
        "min_s": round(min(lat), 2),
        "max_s": round(max(lat), 2),
        "mean_calls": round(statistics.mean([r["calls"] for r in sub]), 1),
        "mean_in_tok": round(statistics.mean([r["in_tok"] for r in sub])),
        "mean_out_tok": round(statistics.mean([r["out_tok"] for r in sub])),
        "mean_cost_usd": round(cost, 5),
    }
    if query is not None:
        agg["query"] = query
    return agg


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="질의·설정당 반복 횟수")
    ap.add_argument("--tool-latency", type=float, default=0.3, help="도구 모의 지연(초)")
    args = ap.parse_args()

    global TOOL_LATENCY
    TOOL_LATENCY = args.tool_latency

    print("===== 1부: 규칙 분류 정확도 (LLM 호출 없음) =====")
    classification = score_classification()
    print(json.dumps(classification, ensure_ascii=False, indent=2))

    tools = _stub_tools_by_server()
    # 오케스트레이터는 현행 기본값(on)으로 고정, Fast Router 축만 분리
    graphs = {}
    for label, fast in CONFIGS:
        graphs[label] = await build_agent_graph(
            tools_by_server=tools,
            grounding_enabled=False,
            suggestions_enabled=False,
            orchestrator_enabled=True,
            fast_router_enabled=fast,
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
                    f"[{label}] {qname} rep{rep}: {r['latency']:.2f}s "
                    f"calls={r['calls']} tok={r['in_tok']}/{r['out_tok']}"
                )

    print("\n===== 2부: 설정별 집계 =====")
    summary = [_aggregate(rows, label) for label, _ in CONFIGS]
    for agg in summary:
        print(json.dumps(agg, ensure_ascii=False))

    per_query = [_aggregate(rows, label, qname) for label, _ in CONFIGS for qname, _ in QUERIES]
    print("\n===== 질의별 집계 =====")
    for agg in per_query:
        print(json.dumps(agg, ensure_ascii=False))

    # 잡담 질의만 뽑은 바이패스 이득, 진단 질의는 회귀 가드라 제외
    smalltalk = [r for r in rows if r["query"].startswith("smalltalk")]

    def _mean(sub, key):
        return statistics.mean([r[key] for r in sub])

    off = [r for r in smalltalk if r["config"] == "fast_router_off"]
    on = [r for r in smalltalk if r["config"] == "fast_router_on"]
    delta = {}
    if off and on:

        def pct(b, a):
            return round((b - a) / b * 100, 1) if b else 0.0

        delta = {
            "latency_reduction_pct": pct(_mean(off, "latency"), _mean(on, "latency")),
            "calls_reduction_pct": pct(_mean(off, "calls"), _mean(on, "calls")),
            "in_tok_reduction_pct": pct(_mean(off, "in_tok"), _mean(on, "in_tok")),
        }
        print("\n===== 잡담 질의 바이패스 이득 (off 대비 on) =====")
        print(json.dumps(delta, ensure_ascii=False))

    with open("scripts/bench/fast_router_result.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "tool_latency": TOOL_LATENCY,
                "reps": args.reps,
                "rate_in_usd_per_1m": RATE_IN,
                "rate_out_usd_per_1m": RATE_OUT,
                "classification": classification,
                "rows": rows,
                "summary": summary,
                "per_query": per_query,
                "smalltalk_delta": delta,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print("\n결과 저장: scripts/bench/fast_router_result.json")


if __name__ == "__main__":
    asyncio.run(main())
