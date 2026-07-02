"""벡터 컬렉션 모델 (DEV_VECTORDB), 요구사항 정의서 §8.3 스키마 기준

pgvector 확장 필요 (docker-compose의 pgvector 이미지 사용)
임베딩 차원(기본 1536)은 settings.embedding_dim과 일치 필수
TODO(김건): 임베딩 모델 선정 후 차원을 마이그레이션으로 확정
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class KnowledgeChunk(Base):
    """장애 조치 매뉴얼 벡터 컬렉션 (knowledge_collection)"""

    __tablename__ = "knowledge_collection"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(50), index=True)  # 예: MAN-ETC-042
    equipment_type: Mapped[str | None] = mapped_column(String(50), index=True)  # 예: Etching
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
