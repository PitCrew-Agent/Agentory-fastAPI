"""알림 모델 (NEW_PROACT01_ALERT01)

설비 알람 이력 + 읽음 상태 영속, 동일 설비+변수+알람은 30분 버킷당 1건만 적재해
반복 알람의 난잡한 중복을 억제 (NEW_PROACT01_ALERT03)
source_alarm_id는 버킷 첫 EquipmentAlarm 참조, watcher 선제 알림(NEW_PROACT01_DETECT01)은 NULL
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
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
        # 담당 라인 스코핑 조회용, 이력 페이지네이션 정렬키와 복합 (BE_NOTI01_SCOPE01)
        Index("ix_notifications_line_occurred_at", "line_name", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equipment_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # 알림 발생 설비의 소속 라인(EquipmentMaster.line_name = lines.code), 담당 라인 스코핑 기준
    # 설비 마스터에 없는 설비는 NULL, 라인 미배정 사용자와 동일하게 조회 대상에서 제외
    line_name: Mapped[str | None] = mapped_column(String(50))
    # 알람이 발생한 센서 변수 키, watcher 선제 알림 등 변수 특정 불가 시 NULL
    metric: Mapped[str | None] = mapped_column(String(20))
    alarm_code: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # 발생 시각의 30분 절단값(00분·30분 경계), 동일 설비+변수+알람 중복 억제 유니크 키
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_alarm_id: Mapped[int | None] = mapped_column(
        BigInteger
    )  # 버킷 첫 EquipmentAlarm alarm_id, watcher는 NULL
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationRead(Base):
    """알림 사용자별 읽음 상태 (BE_NOTI01_SCOPE01)

    읽음은 사용자마다 다르므로 알림 행의 플래그가 아니라 (알림, 사용자) 연결로 보관
    행이 존재하면 읽음, 없으면 미읽음, 읽음 해제는 행 삭제
    """

    __tablename__ = "notification_reads"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "user_id", name="uq_notification_reads_notification_user"
        ),
        # 사용자별 미읽음 판정(NOT EXISTS) 조회용
        Index("ix_notification_reads_user_notification", "user_id", "notification_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
