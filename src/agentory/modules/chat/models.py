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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class ChatSession(Base):
    """대화 세션"""

    __tablename__ = "chat_session"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_sub: Mapped[str] = mapped_column(String(255), nullable=False)  # Keycloak sub
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
