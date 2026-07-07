"""POC-C: OpenAI 역할별 모델 호출·지연·토큰 검증 (검증 후 폐기)

성공 기준: router/worker 모델 응답 성공 + 지연·토큰 측정 + finalizer 후보 비교
실행: uv run python scripts/poc/poc_c_models.py
"""

import asyncio
import time

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from agentory.core.config import get_settings

PROMPT = "제조 라인 A의 온도 이상 여부를 한 문장으로 요약해줘"

# 역할: (라벨, 모델명), settings 기반 + finalizer 후보
settings = get_settings()
TARGETS = [
    ("router", settings.llm_router_model or "gpt-5-nano"),
    ("worker", settings.llm_model),
    ("finalizer 후보", settings.llm_finalizer_model or "gpt-5.1"),
]


async def call_one(role: str, model: str) -> dict:
    llm = ChatOpenAI(model=model, api_key=settings.openai_api_key)
    start = time.perf_counter()
    try:
        resp = await llm.ainvoke([HumanMessage(content=PROMPT)])
    except Exception as exc:
        return {"role": role, "model": model, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    latency = time.perf_counter() - start
    usage = resp.response_metadata.get("token_usage", {})
    return {
        "role": role,
        "model": model,
        "ok": True,
        "latency": round(latency, 2),
        "in_tok": usage.get("prompt_tokens"),
        "out_tok": usage.get("completion_tokens"),
        "preview": (resp.content or "")[:60].replace("\n", " "),
    }


async def main() -> None:
    print(f"[POC-C] 프롬프트: {PROMPT}\n")
    results = await asyncio.gather(*(call_one(r, m) for r, m in TARGETS))
    print(f"{'역할':<12} {'모델':<14} {'성공':<5} {'지연(s)':<8} {'in':<6} {'out':<6}")
    print("-" * 60)
    for r in results:
        if r["ok"]:
            print(
                f"{r['role']:<12} {r['model']:<14} {'O':<5} "
                f"{r['latency']:<8} {str(r['in_tok']):<6} {str(r['out_tok']):<6}"
            )
        else:
            print(f"{r['role']:<12} {r['model']:<14} {'X':<5} {r['error']}")
    print()
    for r in results:
        if r["ok"]:
            print(f"  [{r['role']}] {r['preview']}")


if __name__ == "__main__":
    asyncio.run(main())
