"""벡터 컬렉션 모델 (DEV_VECTORDB), 요구사항 정의서 §8.3 스키마 기준

pgvector 확장 필요 (docker-compose의 pgvector 이미지 사용)
embedding 차원은 settings.embedding_dim과 일치 필수
EMBEDDING_DIM은 KURE-v1 dense 기준 1024 (AI_RAG01_CHUNK01, 기존 text-embedding-3-small은 1536)
임베딩 모델 변경 시 컬럼 차원 마이그레이션과 전체 재적재 필요
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base

EMBEDDING_DIM = 1024


class KnowledgeChunk(Base):
    """장애 조치 매뉴얼 벡터 컬렉션 (knowledge_collection)

    타 테이블과 FK 없는 벡터 검색 전용 컬렉션
    embedding에 HNSW(코사인) 인덱스, equipment_type·alarm_code는 메타 필터용
    """

    __tablename__ = "knowledge_collection"
    __table_args__ = (
        Index(
            "ix_knowledge_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_knowledge_equipment_type", "equipment_type"),
        Index("ix_knowledge_alarm_code", "alarm_code"),
    )

    chunk_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(50), nullable=False)  # 예: MAN-ETC-042
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 문서 내 청크 순서
    equipment_type: Mapped[str | None] = mapped_column(String(50))  # 예: Etching
    alarm_code: Mapped[str | None] = mapped_column(String(20))  # 예: ERR-402
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
