"""대화 세션·메시지 모델 (BE_CHAT01_QUERY01)

user_sub는 Keycloak OIDC subject, 로컬 user 테이블은 타 담당이라 FK 없이 저장
trace에 추론 기록(reasoning_steps·tool_calls)을 jsonb로 적재
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class ChatSession(Base):
    """대화 세션"""

    __tablename__ = "chat_session"
    # 미삭제 세션의 사용자별 최신순 히스토리 조회 대응, soft delete 제외 (BE_CHAT03_HISTORY01)
    __table_args__ = (
        Index(
            "ix_chat_session_user_created",
            "user_sub",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_sub: Mapped[str] = mapped_column(String(255), nullable=False)  # Keycloak sub
    # 대화 컨텍스트 설비, 세션 생성 시 첫 질의의 선택 설비로 고정 (NEW_TWIN01_CHATCTX01)
    # 대시보드는 항상 설비를 선택해 넘기므로 신규 세션은 값 존재, 레거시 세션은 NULL 허용
    equipment_id: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 히스토리 삭제는 soft delete, NULL=활성, 목록·상세에서 제외 (BE_CHAT03_DELETE01)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """대화 메시지, role은 user/assistant/system/tool 제한"""

    __tablename__ = "chat_message"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_chat_message_role"
        ),
        Index("ix_chat_message_session_time", "session_id", "created_at"),
    )

    message_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_session.session_id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB().with_variant(JSON, "sqlite"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
