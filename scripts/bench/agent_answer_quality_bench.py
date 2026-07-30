"""에이전트 답변 품질 A/B 벤치 (AI_AGENT01_QUALITY01)

레거시 Supervisor+워커(orchestrator off) vs 현재 Planner+병렬 Fetch(orchestrator on)를
data/eval 평가 셋 45건으로 실행, 설정별 답변·인용·도구 호출과 운영 지표를 실측하고
결정적 품질 지표(라우팅 정확도·키워드 포함률·인용률·환각율·정직 보고율)를 산출한다.

이 스크립트가 저장하는 원자료(answer·citations 포함)는 pairwise·faithfulness LLM 판정
(agent_pairwise_judge.py)의 입력이 된다. 추정 지표(win-rate 등)는 여기서 계산하지 않는다.

실행: uv run python scripts/bench/agent_answer_quality_bench.py --reps 2 --concurrency 4
전제: mcp-realtime(:8101)·mcp-knowledge(:8102)·mcp-maintenance(:8103) 기동, DB 시드·매뉴얼 적재,
OPENAI_API_KEY. 지연은 동시 실행(concurrency)의 영향을 받으므로 권위 지연은 ADR-0009를 따른다.
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import yaml
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from agentory.modules.agent.mcp_client.client import load_tools_by_server
from agentory.modules.agent.runner import RECURSION_LIMIT, initial_state
from agentory.modules.agent.supervisor.graph import build_agent_graph

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_DIR = ROOT / "data" / "eval"
DEFAULT_OUT = ROOT / "scripts" / "bench" / "agent_answer_quality_result.json"

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


def load_cases(eval_dir: Path) -> list[dict[str, Any]]:
    """data/eval/*.yaml 평가 셋 전체 로드"""
    cases: list[dict[str, Any]] = []
    for path in sorted(eval_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data:
            cases.extend(data if isinstance(data, list) else [data])
    return cases


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


def score_case(case: dict[str, Any], tools: list[str], answer: str, cited: list[str]) -> dict:
    """결정적 품질 지표 산정, 판정 규칙은 정직하게 크게 잡음

    routing_ok: 기대 도구 호출 여부 (no_tools·any·all·forbidden 규칙)
    keyword_ok: 정답 키워드 포함 (any·all)
    citation_ok: 기대 doc_id 인용 여부 (있는 케이스만)
    hallucinated: must_not_contain 문자열 등장 여부 (등장 자체가 환각인 케이스만)
    honest_ok: 정직 보고 케이스에서 정직 마커(answer_contains_any) 포함 여부
    """
    exp = case.get("expect", {}) or {}
    tools_set = set(tools)

    if exp.get("no_tools"):
        routing_ok = len(tools_set) == 0
    else:
        any_tools = set(exp.get("tools_called_any") or [])
        all_tools = set(exp.get("tools_called_all") or [])
        forbid = set(exp.get("tools_forbidden") or [])
        any_ok = (not any_tools) or bool(tools_set & any_tools)
        all_ok = (not all_tools) or all_tools <= tools_set
        forbid_ok = (not forbid) or not (tools_set & forbid)
        routing_ok = any_ok and all_ok and forbid_ok

    contains_any = exp.get("answer_contains_any") or []
    contains_all = exp.get("answer_contains_all") or []
    kw_any_ok = (not contains_any) or any(k in answer for k in contains_any)
    kw_all_ok = (not contains_all) or all(k in answer for k in contains_all)
    keyword_ok = kw_any_ok and kw_all_ok

    citations_include = exp.get("citations_include") or []
    has_citation_label = bool(citations_include)
    citation_ok = (not has_citation_label) or bool(set(citations_include) & set(cited))

    must_not = exp.get("must_not_contain") or []
    has_mustnot = bool(must_not)
    hallucinated = has_mustnot and any(s in answer for s in must_not)

    is_honest = bool(exp.get("expect_honest_empty"))
    honest_ok = (not is_honest) or keyword_ok

    return {
        "routing_ok": routing_ok,
        "keyword_ok": keyword_ok,
        "citation_ok": citation_ok,
        "has_citation_label": has_citation_label,
        "hallucinated": hallucinated,
        "has_mustnot": has_mustnot,
        "is_honest": is_honest,
        "honest_ok": honest_ok,
    }


async def run_once(graph, case: dict[str, Any]) -> dict:
    meter = Meter()
    state = initial_state(case["query"], history=[], equipment_id=None)
    t0 = time.monotonic()
    error = None
    try:
        final = await graph.ainvoke(
            state, config={"recursion_limit": RECURSION_LIMIT, "callbacks": [meter]}
        )
        tools = _tools_called(final["messages"])
        answer = _final_answer(final["messages"])
        cited = [c.get("doc_id") for c in (final.get("citations") or []) if c.get("doc_id")]
        steps = final.get("step_count", 0)
    except Exception as exc:  # noqa: BLE001 벤치는 케이스 실패를 삼키고 계속 진행
        error = f"{type(exc).__name__}: {exc}"
        tools, answer, cited, steps = [], "", [], 0
    dt = time.monotonic() - t0

    scored = score_case(case, tools, answer, cited)
    return {
        "id": case["id"],
        "category": case.get("category", "?"),
        "latency": dt,
        "calls": meter.calls,
        "in_tok": meter.in_tok,
        "out_tok": meter.out_tok,
        "steps": steps,
        "tools": sorted(set(tools)),
        "citations": cited,
        "answer": answer,
        "error": error,
        **scored,
    }


def _rate(rows: list[dict], key: str, subset=None) -> float | None:
    sub = [r for r in rows if subset is None or subset(r)]
    if not sub:
        return None
    return round(sum(bool(r[key]) for r in sub) / len(sub), 3)


def _cost(rows: list[dict]) -> float:
    return statistics.mean(
        [(r["in_tok"] * RATE_IN + r["out_tok"] * RATE_OUT) / 1_000_000 for r in rows]
    )


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for cfg, _ in CONFIGS:
        sub = [r for r in rows if r["config"] == cfg]
        out.append(
            {
                "config": cfg,
                "n": len(sub),
                "routing_acc": _rate(sub, "routing_ok"),
                "keyword_rate": _rate(sub, "keyword_ok"),
                "citation_rate": _rate(sub, "citation_ok", lambda r: r["has_citation_label"]),
                "honest_empty_acc": _rate(sub, "honest_ok", lambda r: r["is_honest"]),
                "hallucination_rate": _rate(sub, "hallucinated", lambda r: r["has_mustnot"]),
                "error_rate": round(sum(r["error"] is not None for r in sub) / len(sub), 3),
                "p50_latency_s": round(statistics.median([r["latency"] for r in sub]), 1),
                "mean_calls": round(statistics.mean([r["calls"] for r in sub]), 1),
                "mean_in_tok": round(statistics.mean([r["in_tok"] for r in sub])),
                "mean_out_tok": round(statistics.mean([r["out_tok"] for r in sub])),
                "mean_cost_usd": round(_cost(sub), 5),
            }
        )
    return out


def summarize_by_category(rows: list[dict]) -> list[dict]:
    cats = sorted({r["category"] for r in rows})
    out = []
    for cfg, _ in CONFIGS:
        for cat in cats:
            sub = [r for r in rows if r["config"] == cfg and r["category"] == cat]
            if not sub:
                continue
            out.append(
                {
                    "config": cfg,
                    "category": cat,
                    "n": len(sub),
                    "routing_acc": _rate(sub, "routing_ok"),
                    "keyword_rate": _rate(sub, "keyword_ok"),
                    "honest_empty_acc": _rate(sub, "honest_ok", lambda r: r["is_honest"]),
                    "hallucination_rate": _rate(sub, "hallucinated", lambda r: r["has_mustnot"]),
                }
            )
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2, help="케이스·설정당 반복 횟수")
    ap.add_argument("--concurrency", type=int, default=4, help="동시 실행 수 (지연 측정에 영향)")
    ap.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    cases = load_cases(args.eval_dir)
    print(f"평가 셋 {len(cases)}건 로드: {args.eval_dir}")

    tools_by_server = await load_tools_by_server()
    graphs = {}
    for cfg, orch in CONFIGS:
        graphs[cfg] = await build_agent_graph(
            tools_by_server=tools_by_server,
            grounding_enabled=False,
            suggestions_enabled=False,
            orchestrator_enabled=orch,
        )

    sem = asyncio.Semaphore(args.concurrency)

    async def _task(cfg: str, case: dict, rep: int) -> dict:
        async with sem:
            r = await run_once(graphs[cfg], case)
            r["config"] = cfg
            r["rep"] = rep
            mark = "ERR" if r["error"] else ("ok" if r["routing_ok"] and r["keyword_ok"] else "..")
            print(
                f"[{cfg}] {r['id']} rep{rep} {mark}: {r['latency']:.1f}s "
                f"calls={r['calls']} route={r['routing_ok']} kw={r['keyword_ok']} "
                f"cite={r['citations']} halluc={r['hallucinated']}"
            )
            return r

    jobs = [
        _task(cfg, case, rep) for cfg, _ in CONFIGS for case in cases for rep in range(args.reps)
    ]
    rows = await asyncio.gather(*jobs)

    summary = summarize(rows)
    by_cat = summarize_by_category(rows)

    print("\n===== 설정별 종합 (결정적 지표) =====")
    for agg in summary:
        print(json.dumps(agg, ensure_ascii=False))
    print("\n===== 카테고리별 =====")
    for agg in by_cat:
        print(json.dumps(agg, ensure_ascii=False))

    payload = {
        "rate_in_usd_per_1m": RATE_IN,
        "rate_out_usd_per_1m": RATE_OUT,
        "reps": args.reps,
        "concurrency": args.concurrency,
        "n_cases": len(cases),
        "rows": rows,
        "summary": summary,
        "by_category": by_cat,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
