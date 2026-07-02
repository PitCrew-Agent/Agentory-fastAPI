"""MCP knowledge 서버 (BE_MCP01_SERVER01), 지식베이스 검색(RAG) 도구

실행: uv run mcp-knowledge (streamable-http, 포트 8102)
검색 로직은 agentory.modules.rag 포트(Embedder, VectorStore) 재사용
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agentory-knowledge", host="0.0.0.0", port=8102)


@mcp.tool()
async def search_manuals(
    query: str,
    top_k: int = 3,
    equipment_type: str | None = None,
) -> list[dict[str, Any]]:
    """자연어 질문 임베딩 후 매뉴얼 벡터 컬렉션에서 유사도 Top-K 검색

    (BE_MCP04_RAG01) 반환: [{doc_id, content, score}]
    유사도 임계값 미달 시 "관련 문서 없음" 반환 (환각 방지)
    """
    # TODO(김건): rag.embedding + rag.store 연동
    raise NotImplementedError


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
