"""RAG 검색 품질 평가 실행 (AI_RAG02_EVAL01)

실행: uv run python scripts/eval_rag.py --k 3
OPENAI_API_KEY, DB, 매뉴얼 적재 필요
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any

import yaml

from agentory.modules.rag.embedding import get_embedder
from agentory.modules.rag.eval import evaluate_retrieval
from agentory.modules.rag.store.pgvector import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "golden"


def _load_golden_cases(golden_dir: Path) -> list[dict[str, Any]]:
    """골든 질의 셋 YAML 전체 로드"""
    cases: list[dict[str, Any]] = []
    for path in sorted(golden_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            continue
        cases.extend(data if isinstance(data, list) else [data])
    return cases


def _rag_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """rag_hit_docs 보유 케이스 필터"""
    return [case for case in cases if case.get("expect", {}).get("rag_hit_docs")]


def _csv(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def _print_report(report: dict[str, Any]) -> None:
    k = report["k"]
    print("RAG 검색 품질 평가")
    print(f"cases: {report['n']}")
    print(f"hit@{k}: {report['hit@k']:.3f}")
    print(f"recall@{k}: {report['recall@k']:.3f}")
    print(f"mrr: {report['mrr']:.3f}")
    print()
    print("case_id | hit | recall | rr | expected | retrieved")
    for item in report["per_case"]:
        print(
            f"{item['id']} | {item['hit']:.3f} | {item['recall']:.3f} | "
            f"{item['rr']:.3f} | {_csv(item['expected'])} | {_csv(item['retrieved'])}"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 검색 품질 평가")
    parser.add_argument("--k", type=int, default=3, help="Top-K 검색 개수")
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="유사도 임계값, 미지정 시 필터 없음 (운영 재현은 RAG_SEARCH_MIN_SCORE 값 지정)",
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=DEFAULT_GOLDEN_DIR,
        help="골든 질의 셋 디렉터리",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    cases = _rag_cases(_load_golden_cases(args.golden_dir))
    if not cases:
        print(f"rag_hit_docs 케이스 없음: {args.golden_dir}")
        return

    report = await evaluate_retrieval(
        cases,
        embedder=get_embedder(),
        store=PgVectorStore(),
        k=args.k,
        min_score=args.min_score,
    )
    if args.min_score is not None:
        print(f"min_score: {args.min_score}")
    _print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
