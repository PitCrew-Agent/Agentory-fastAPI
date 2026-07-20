"""MCP knowledge 서버 (BE_MCP01_SERVER01), 지식베이스 검색(RAG) 도구

실행: uv run mcp-knowledge (streamable-http, 포트 8102)
검색 로직은 agentory.modules.rag 포트(Embedder, VectorStore) 재사용
"""

import asyncio
import errno
import logging
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from agentory.core.config import get_settings
from agentory.modules.rag.embedding import get_embedder
from agentory.modules.rag.prefetch import prefetch_models
from agentory.modules.rag.rerank import get_reranker
from agentory.modules.rag.store.pgvector import PgVectorStore, verify_embedding_dim

log = logging.getLogger(__name__)

mcp = FastMCP("agentory-knowledge", host="0.0.0.0", port=8102)

# LLM이 과대 top_k를 넣는 것 방지, 튜닝 대상 아닌 정적 가드라 상수 유지
MAX_TOP_K = 20
_COUNT_QUERY_TOP_K = 6
_ALARM_CODE_COUNT_QUERY = re.compile(r"알람\s*코드.*(몇\s*개|개수|갯수|수량|목록)")

# 임베더·스토어·리랭커는 검색마다 재생성하지 않고 프로세스 단위로 재사용(초기화 비용 절감)
_embedder = None
_store = None
_reranker = None
_reranker_ready = False  # provider=none이면 리랭커가 None이므로 초기화 여부를 플래그로 구분


def _get_search_deps():
    global _embedder, _store, _reranker, _reranker_ready
    if _embedder is None:
        _embedder = get_embedder()
    if _store is None:
        _store = PgVectorStore()
    if not _reranker_ready:
        _reranker = get_reranker()  # provider=none이면 None
        _reranker_ready = True
    return _embedder, _store, _reranker


def _above_threshold(results: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    # 임계값 이상만 유지, 전부 미달이면 빈 목록(관련 문서 없음)
    return [item for item in results if item["score"] >= threshold]


def _expand_alarm_code_count_query(query: str, top_k: int | None) -> tuple[str, int | None]:
    if not _ALARM_CODE_COUNT_QUERY.search(query):
        return query, top_k
    search_query = f"{query} 적용 알람 코드 목록"
    resolved_top_k = max(top_k or 0, _COUNT_QUERY_TOP_K)
    return search_query, resolved_top_k


@mcp.tool()
async def search_manuals(
    query: str,
    top_k: int | None = None,
    equipment_type: str | None = None,
) -> list[dict[str, Any]]:
    """자연어 질문 임베딩 후 매뉴얼 벡터 컬렉션에서 유사도 Top-K 검색

    (BE_MCP04_RAG01) 반환: [{doc_id, content, score}]
    top_k 미지정 시 설정값(RAG_SEARCH_TOP_K) 사용
    유사도 임계값(RAG_SEARCH_MIN_SCORE) 미달 시 빈 배열 반환
    빈 배열은 매뉴얼 부재 단정이 아니라 현재 질의 기준 임계값 이상 근거 없음을 뜻함
    """
    if not query.strip():
        raise ValueError("query는 비어있을 수 없음")
    if top_k is not None and not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k는 1 이상 {MAX_TOP_K} 이하여야 함")
    settings = get_settings()
    query, count_top_k = _expand_alarm_code_count_query(query, top_k)
    resolved_top_k = count_top_k or settings.rag_search_top_k
    if not 1 <= resolved_top_k <= MAX_TOP_K:
        raise ValueError(f"top_k는 1 이상 {MAX_TOP_K} 이하여야 함")
    embedder, store, reranker = _get_search_deps()
    embedding = await embedder.embed_query(query)
    # 리랭커가 있으면 top_n 후보를 받아 재정렬, 없으면 top_k 그대로 검색
    pool_k = max(resolved_top_k, settings.reranker_top_n) if reranker else resolved_top_k
    results = await store.search(embedding, top_k=pool_k, equipment_type=equipment_type)
    # 코사인 임계값 필터를 먼저 적용(리랭크 점수는 척도가 달라 임계 기준이 아님), 이후 재정렬
    results = _above_threshold(results, threshold=settings.rag_search_min_score)
    if reranker and results:
        try:
            results = await reranker.rerank(query, results, top_k=resolved_top_k)
        except Exception:
            log.exception("리랭커 실패, 벡터 검색 결과 사용")
            results = results[:resolved_top_k]
    else:
        results = results[:resolved_top_k]
    return results


async def search_similar_cases(
    situation_summary: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """과거 장애·조치 이력에서 현재 이상 상황과 유사한 사례 Top-K 검색

    (NEW_CASE01_SEARCH01, 선택 기능) 반환: [{case_id, 증상, 조치, 결과}]
    미구현 스텁이 tool binding에 노출되면 불필요한 실패 Observation이 생기므로
    구현 전까지 MCP 미등록 상태 유지
    """
    # TODO(김건): case_collection 적재 후 구현하고 @mcp.tool() 재등록
    raise NotImplementedError


def _startup() -> None:
    # DB 벡터 차원 정합성 검증(B), 불일치면 기동 차단, DB 미연결 등은 경고 후 진행
    try:
        asyncio.run(verify_embedding_dim())
    except RuntimeError as exc:
        log.error("임베딩 차원 불일치로 mcp-knowledge 기동 거부: %s", exc)
        raise
    except Exception as exc:
        log.warning("임베딩 차원 검증 건너뜀(DB 미연결 등): %s", exc)
    # config 모델 가중치 확보(멱등, 변경·신규만 다운로드), 실패해도 기동은 계속(첫 질의 lazy 재시도)
    try:
        prefetch_models()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            log.error("가중치 프리페치 실패: 디스크 용량 부족(ENOSPC), 첫 질의 시 재시도")
        else:
            log.warning("가중치 프리페치 실패(OS errno=%s): %s, 첫 질의 시 재시도", exc.errno, exc)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None:
            log.error("가중치 프리페치 실패: HF HTTP %s, 첫 질의 시 재시도", status)
        else:
            log.warning("가중치 프리페치 실패(연결 등): %s, 첫 질의 시 재시도", exc)


def run() -> None:
    _startup()
    mcp.run(transport="streamable-http")
