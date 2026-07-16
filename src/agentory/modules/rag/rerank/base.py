"""리랭커 포트, 교체 가능 지점 ③ (비기능: 검색 정밀도)

검색된 후보 문서를 query 기준으로 재정렬해 상위 top_k만 반환
기본 구현은 rerank/cross_encoder.py의 CrossEncoderReranker, 다른 모델도 이 패키지에 추가
"""

from typing import Any, Protocol


class Reranker(Protocol):
    async def rerank(
        self, query: str, documents: list[dict[str, Any]], top_k: int
    ) -> list[dict[str, Any]]:
        """query 기준으로 documents를 재정렬해 상위 top_k 반환

        documents는 [{doc_id, content, score}] 형태, 반환 문서의 score는 재랭킹 점수로 갱신
        (코사인 유사도가 아니라 cross-encoder 관련도 점수)
        """
        ...
