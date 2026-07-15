"""알림 모델 (NEW_PROACT01_ALERT01)

설비 알람 이력 + 읽음 상태 영속, 동일 설비+변수+알람은 30분 버킷당 1건만 적재해
반복 알람의 난잡한 중복을 억제 (NEW_PROACT01_ALERT03)
source_alarm_id는 버킷 첫 EquipmentAlarm 참조, watcher 선제 알림(NEW_PROACT01_DETECT01)은 NULL
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
    """알림 이력 항목, 발생 시각·설비·변수·알람 코드·메시지·읽음 상태 보관"""

    __tablename__ = "notifications"
    __table_args__ = (
        # 동일 설비+변수+알람은 30분 버킷당 1건만 허용해 반복 알람 중복 억제(멱등 sync)
        UniqueConstraint(
            "equipment_id",
            "metric",
            "alarm_code",
            "bucket_start",
            name="uq_notifications_equip_metric_alarm_bucket",
        ),
        Index("ix_notifications_occurred_at", "occurred_at"),
        Index("ix_notifications_is_read", "is_read"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equipment_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # 알람이 발생한 센서 변수 키, watcher 선제 알림 등 변수 특정 불가 시 NULL
    metric: Mapped[str | None] = mapped_column(String(20))
    alarm_code: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # 발생 시각의 30분 절단값(00분·30분 경계), 동일 설비+변수+알람 중복 억제 유니크 키
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_alarm_id: Mapped[int | None] = mapped_column(
        BigInteger
    )  # 버킷 첫 EquipmentAlarm alarm_id, watcher는 NULL
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
