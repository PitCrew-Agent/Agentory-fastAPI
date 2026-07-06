"""MCP knowledge 서버 (BE_MCP01_SERVER01), 지식베이스 검색(RAG) 도구

실행: uv run mcp-knowledge (streamable-http, 포트 8102)
검색 로직은 agentory.modules.rag 포트(Embedder, VectorStore) 재사용
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agentory.modules.rag.embedding.openai import get_embedder
from agentory.modules.rag.store.pgvector import PgVectorStore

mcp = FastMCP("agentory-knowledge", host="0.0.0.0", port=8102)

# 코사인 유사도 임계값, 미달 결과 제외로 환각 방지 (실 매뉴얼·골든셋 확보 후 튜닝)
MIN_SCORE = 0.2


def _above_threshold(
    results: list[dict[str, Any]], threshold: float = MIN_SCORE
) -> list[dict[str, Any]]:
    # 임계값 이상만 유지, 전부 미달이면 빈 목록(관련 문서 없음)
    return [item for item in results if item["score"] >= threshold]


@mcp.tool()
async def search_manuals(
    query: str,
    top_k: int = 3,
    equipment_type: str | None = None,
) -> list[dict[str, Any]]:
    """자연어 질문 임베딩 후 매뉴얼 벡터 컬렉션에서 유사도 Top-K 검색

    (BE_MCP04_RAG01) 반환: [{doc_id, content, score}]
    유사도 임계값 미달 시 빈 배열 반환 (관련 문서 없음, 환각 방지)
    """
    if not query.strip():
        raise ValueError("query는 비어있을 수 없음")
    embedder = get_embedder()
    store = PgVectorStore()
    [embedding] = await embedder.embed([query])
    results = await store.search(embedding, top_k=top_k, equipment_type=equipment_type)
    return _above_threshold(results)


@mcp.tool()
async def search_similar_cases(
    situation_summary: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """과거 장애·조치 이력에서 현재 이상 상황과 유사한 사례 Top-K 검색

    (NEW_CASE01_SEARCH01, 선택 기능) 반환: [{case_id, 증상, 조치, 결과}]
    """
    # TODO(김건): case_collection 적재 후 구현
    raise NotImplementedError


def run() -> None:
    mcp.run(transport="streamable-http")
