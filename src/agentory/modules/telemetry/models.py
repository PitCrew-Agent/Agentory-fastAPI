"""정형 RDB 모델 (DEV_DATABASE), 요구사항 정의서 §8 스키마 기준"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class EquipmentMaster(Base):
    """설비 마스터 (§8.1)"""

    __tablename__ = "equipment_master"

    equipment_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    line_name: Mapped[str] = mapped_column(String(50), nullable=False)
    process_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(50))
    manager_dept: Mapped[str | None] = mapped_column(String(50))


class EquipmentTelemetry(Base):
    """설비 로그·센서 (§8.2)"""

    __tablename__ = "equipment_telemetry"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_master.equipment_id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pressure: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    alarm_code: Mapped[str | None] = mapped_column(String(20))
