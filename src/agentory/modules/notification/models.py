"""알림 모델 (NEW_PROACT01_ALERT01)

설비 알람 이력 + 읽음 상태 영속, source_log_id로 telemetry 알람 멱등 동기화
watcher 선제 알림(NEW_PROACT01_DETECT01)은 source_log_id NULL로 동일 테이블 적재
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class Notification(Base):
    """알림 이력 항목, 발생 시각·설비·알람 코드·메시지·읽음 상태 보관"""

    __tablename__ = "notifications"
    __table_args__ = (
        # telemetry 로그 1건당 알림 1건 보장(멱등 sync), watcher 알림은 NULL 다건 허용
        UniqueConstraint("source_log_id", name="uq_notifications_source_log"),
        Index("ix_notifications_occurred_at", "occurred_at"),
        Index("ix_notifications_is_read", "is_read"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equipment_id: Mapped[str] = mapped_column(String(50), nullable=False)
    alarm_code: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    source_log_id: Mapped[int | None] = mapped_column(
        BigInteger
    )  # telemetry log_id, watcher는 NULL
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
