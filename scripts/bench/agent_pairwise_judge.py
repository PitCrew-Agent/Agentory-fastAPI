"""답변 품질 LLM 판정 (추정 지표) (AI_AGENT01_QUALITY01)

agent_answer_quality_bench.py가 저장한 원자료(설정별 answer·citations)를 입력으로
레거시 vs 현재 구조의 답변을 LLM judge로 채점한다. 생성 모델(gpt-5-mini)과 분리된 더 강한
심사 모델을 써 자기선호(self-preference) 편향을 완화한다.

세 축:
  1) Pairwise A/B win-rate  blind + 위치 스왑 2회, 현재(after) 선호 승률 (헤드라인)
  2) Rubric 1~5            정확성·근거·완결성·간결성, 참고용 이상적 답변 요지 제공
  3) Faithfulness          질의로 실제 검색한 근거 청크 대비 답변 주장 지지도 1~5

모두 추정치라 케이스 부트스트랩 신뢰구간(95%)을 함께 보고한다.

실행: uv run python scripts/bench/agent_pairwise_judge.py --judge-model gpt-5 --bootstrap 2000
전제: agent_answer_quality_result.json 존재, OPENAI_API_KEY, (faithfulness는 DB·매뉴얼 적재)
"""

import argparse
import asyncio
import json
import random
import statistics
from pathlib import Path
from typing import Literal

import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agentory.core.config import get_settings
from agentory.modules.rag.embedding import get_embedder
from agentory.modules.rag.store.pgvector import PgVectorStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = ROOT / "scripts" / "bench" / "agent_answer_quality_result.json"
DEFAULT_EVAL_DIR = ROOT / "data" / "eval"
DEFAULT_OUT = ROOT / "scripts" / "bench" / "agent_quality_judge_result.json"

BEFORE = "supervisor_before"
AFTER = "orchestrator_after"


class Pairwise(BaseModel):
    winner: Literal["A", "B", "tie"] = Field(description="더 나은 답변, 우열 없으면 tie")
    reason: str = Field(description="핵심 근거 한 문장")


class Rubric(BaseModel):
    accuracy: int = Field(ge=1, le=5, description="사실 정확성")
    grounding: int = Field(ge=1, le=5, description="근거 충실성(데이터·매뉴얼 부합)")
    completeness: int = Field(ge=1, le=5, description="질문 요구 충족 완결성")
    conciseness: int = Field(ge=1, le=5, description="군더더기 없는 간결성")


class Faithfulness(BaseModel):
    score: int = Field(ge=1, le=5, description="근거 청크 대비 지지도, 5=완전 지지 1=다수 환각")
    unsupported_claims: int = Field(ge=0, description="근거에 없는 주장 개수")


def load_cases(eval_dir: Path) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for path in sorted(eval_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for c in data if isinstance(data, list) else [data]:
            cases[c["id"]] = c
    return cases


def pick_answers(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """(config, id) → 대표 답변 행, rep0 우선·에러면 성공 rep 대체"""
    by: dict[str, dict[str, dict]] = {BEFORE: {}, AFTER: {}}
    for r in rows:
        cfg, cid = r["config"], r["id"]
        cur = by[cfg].get(cid)
        better = cur is None or (cur.get("error") and not r.get("error"))
        rep0 = r.get("rep") == 0 and (cur is None or cur.get("rep") != 0 or cur.get("error"))
        if better or rep0:
            by[cfg][cid] = r
    return by


def _judge(model: str) -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=model, api_key=s.openai_api_key, reasoning_effort="low")


PAIR_SYS = (
    "너는 제조 설비 대화형 에이전트의 답변 품질을 심사하는 엄격한 심사관이다. "
    "사용자 질문에 대한 두 답변 A·B를 비교해 어느 쪽이 더 나은지 판단하라. "
    "우선순위는 정확성 > 근거 충실 > 완결성 > 간결성이다. 지어낸 설비·수치·코드는 큰 감점이다. "
    "질문이 잘못된 전제나 범위 밖·미존재 대상을 담으면, 그것을 정직하게 바로잡은 답이 우수하다. "
    "길이나 문체가 아니라 내용으로 판단하라. 우열이 실제로 없으면 tie."
)


def _pair_prompt(query: str, gist: str, a: str, b: str) -> str:
    return (
        f"[사용자 질문]\n{query}\n\n"
        f"[참고: 이상적 답변 요지 (힌트, 전부는 아님)]\n{gist or '(없음)'}\n\n"
        f"[답변 A]\n{a or '(빈 답변)'}\n\n"
        f"[답변 B]\n{b or '(빈 답변)'}\n\n"
        "어느 답변이 더 나은가? winner에 A·B·tie로 답하라."
    )


RUBRIC_SYS = (
    "너는 제조 설비 에이전트 답변을 1~5로 채점하는 심사관이다. "
    "정확성·근거 충실성·완결성·간결성을 각각 평가하라. 지어낸 정보는 정확성·근거를 크게 낮춘다."
)


def _rubric_prompt(query: str, gist: str, ans: str) -> str:
    return (
        f"[질문]\n{query}\n\n[이상적 답변 요지(참고)]\n{gist or '(없음)'}\n\n"
        f"[평가 대상 답변]\n{ans or '(빈 답변)'}\n\n각 항목을 1~5로 채점하라."
    )


FAITH_SYS = (
    "너는 답변이 제공된 근거 청크에 충실한지 검증하는 심사관이다. "
    "답변의 사실 주장이 근거 청크로 뒷받침되는지 보고, 근거에 없는 주장(환각)의 수를 센다. "
    "일반 상식이 아닌 설비·수치·절차 주장은 근거가 있어야 한다."
)


def _faith_prompt(query: str, evidence: str, ans: str) -> str:
    return (
        f"[질문]\n{query}\n\n[검색된 근거 청크]\n{evidence}\n\n"
        f"[답변]\n{ans}\n\n답변의 근거 지지도를 1~5로 채점하고 근거에 없는 주장 수를 세라."
    )


async def judge_pairwise(llm, cases, by, sem) -> list[dict]:
    struct = llm.with_structured_output(Pairwise)
    ids = sorted(set(by[BEFORE]) & set(by[AFTER]))

    async def one(cid: str) -> dict:
        case = cases.get(cid, {})
        q, gist = case.get("query", ""), case.get("answer_gist", "")
        a_before = by[BEFORE][cid]["answer"]
        a_after = by[AFTER][cid]["answer"]
        # 위치 편향 제거: 1회차 A=before/B=after, 2회차 스왑
        async with sem:
            v1: Pairwise = await struct.ainvoke(
                [("system", PAIR_SYS), ("human", _pair_prompt(q, gist, a_before, a_after))]
            )
        async with sem:
            v2: Pairwise = await struct.ainvoke(
                [("system", PAIR_SYS), ("human", _pair_prompt(q, gist, a_after, a_before))]
            )
        # v1: A=before,B=after / v2: A=after,B=before → after 관점으로 정규화
        r1 = "after" if v1.winner == "B" else "before" if v1.winner == "A" else "tie"
        r2 = "after" if v2.winner == "A" else "before" if v2.winner == "B" else "tie"
        # 두 판정 합산 점수(after 승=1, tie=0.5), 위치 스왑 평균
        pts = {"after": 1.0, "tie": 0.5, "before": 0.0}
        after_score = (pts[r1] + pts[r2]) / 2
        consistent = r1 == r2
        return {
            "id": cid,
            "category": case.get("category", "?"),
            "order1": r1,
            "order2": r2,
            "after_score": after_score,
            "consistent": consistent,
            "reason1": v1.reason,
        }

    return await asyncio.gather(*[one(cid) for cid in ids])


async def judge_rubric(llm, cases, by, sem) -> list[dict]:
    struct = llm.with_structured_output(Rubric)

    async def one(cfg: str, cid: str) -> dict:
        case = cases.get(cid, {})
        ans = by[cfg][cid]["answer"]
        async with sem:
            r: Rubric = await struct.ainvoke(
                [
                    ("system", RUBRIC_SYS),
                    (
                        "human",
                        _rubric_prompt(case.get("query", ""), case.get("answer_gist", ""), ans),
                    ),
                ]
            )
        return {"config": cfg, "id": cid, "category": case.get("category", "?"), **r.model_dump()}

    jobs = [one(cfg, cid) for cfg in (BEFORE, AFTER) for cid in sorted(by[cfg])]
    return await asyncio.gather(*jobs)


async def judge_faithfulness(llm, cases, by, sem, *, top_k: int) -> list[dict]:
    struct = llm.with_structured_output(Faithfulness)
    embedder = get_embedder()
    store = PgVectorStore()
    # 근거 기대 케이스만 대상 (rag_hit_docs 또는 citations_include)
    target_ids = [
        cid
        for cid, c in cases.items()
        if (c.get("expect", {}) or {}).get("rag_hit_docs")
        or (c.get("expect", {}) or {}).get("citations_include")
    ]

    async def evidence_for(query: str) -> str:
        emb = await embedder.embed_query(query)
        results = await store.search(emb, top_k=top_k)
        return "\n---\n".join(f"[{r.get('doc_id')}] {r.get('content', '')[:800]}" for r in results)

    async def one(cfg: str, cid: str) -> dict | None:
        case = cases.get(cid, {})
        row = by[cfg].get(cid)
        if not row or not row.get("answer") or row.get("error"):
            return None
        ev = await evidence_for(case.get("query", ""))
        async with sem:
            r: Faithfulness = await struct.ainvoke(
                [("system", FAITH_SYS), ("human", _faith_prompt(case["query"], ev, row["answer"]))]
            )
        return {"config": cfg, "id": cid, "category": case.get("category", "?"), **r.model_dump()}

    jobs = [one(cfg, cid) for cfg in (BEFORE, AFTER) for cid in target_ids]
    res = await asyncio.gather(*jobs)
    return [r for r in res if r]


def bootstrap_ci(values: list[float], iters: int, seed: int = 7) -> tuple[float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iters):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters)]
    return (round(statistics.mean(values), 3), round(lo, 3), round(hi, 3))


def _rubric_means(rows: list[dict], cfg: str) -> dict:
    sub = [r for r in rows if r["config"] == cfg]
    dims = ["accuracy", "grounding", "completeness", "conciseness"]
    return {d: round(statistics.mean([r[d] for r in sub]), 2) for d in dims} if sub else {}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-file", type=Path, default=DEFAULT_IN)
    ap.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--judge-model", default="gpt-5")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--faith-top-k", type=int, default=5)
    ap.add_argument("--skip-faithfulness", action="store_true")
    args = ap.parse_args()

    payload = json.loads(args.in_file.read_text(encoding="utf-8"))
    cases = load_cases(args.eval_dir)
    by = pick_answers(payload["rows"])
    print(
        f"판정 대상: before {len(by[BEFORE])}건 / after {len(by[AFTER])}건, "
        f"judge={args.judge_model}"
    )

    llm = _judge(args.judge_model)
    sem = asyncio.Semaphore(args.concurrency)

    pair = await judge_pairwise(llm, cases, by, sem)
    rubric = await judge_rubric(llm, cases, by, sem)
    faith = (
        []
        if args.skip_faithfulness
        else await judge_faithfulness(llm, cases, by, sem, top_k=args.faith_top_k)
    )

    # Pairwise 집계 (after 선호 승률, tie=0.5)
    after_scores = [p["after_score"] for p in pair]
    win_mean, win_lo, win_hi = bootstrap_ci(after_scores, args.bootstrap)
    strict_after = sum(1 for p in pair if p["after_score"] > 0.5)
    strict_before = sum(1 for p in pair if p["after_score"] < 0.5)
    ties = sum(1 for p in pair if p["after_score"] == 0.5)
    consistency = round(sum(p["consistent"] for p in pair) / len(pair), 3) if pair else 0.0

    # Faithfulness 집계 (config별 평균·CI)
    faith_summary = {}
    for cfg in (BEFORE, AFTER):
        vals = [float(f["score"]) for f in faith if f["config"] == cfg]
        m, lo, hi = bootstrap_ci(vals, args.bootstrap)
        unsup = [f["unsupported_claims"] for f in faith if f["config"] == cfg]
        faith_summary[cfg] = {
            "n": len(vals),
            "mean_score": m,
            "ci95": [lo, hi],
            "mean_unsupported": round(statistics.mean(unsup), 2) if unsup else None,
        }

    summary = {
        "judge_model": args.judge_model,
        "n_pairwise": len(pair),
        "pairwise_after_winrate": win_mean,
        "pairwise_after_winrate_ci95": [win_lo, win_hi],
        "pairwise_after_wins": strict_after,
        "pairwise_before_wins": strict_before,
        "pairwise_ties": ties,
        "pairwise_position_consistency": consistency,
        "rubric_before": _rubric_means(rubric, BEFORE),
        "rubric_after": _rubric_means(rubric, AFTER),
        "faithfulness": faith_summary,
    }

    print("\n===== 판정 요약 (추정 지표, 95% CI) =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    args.out.write_text(
        json.dumps(
            {"summary": summary, "pairwise": pair, "rubric": rubric, "faithfulness": faith},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n결과 저장: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
