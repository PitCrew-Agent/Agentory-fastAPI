"""정형 RDB 모델 (DEV_DATABASE), 요구사항 정의서 §8 스키마 기준

rf_power·gas_flow는 에칭 장비 특성 반영 확장 컬럼 (ERD v2)
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Numeric, String, func
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
    """설비 로그·센서 (§8.2)

    (equipment_id, timestamp) 복합 인덱스로 설비별 기간 조회 대응
    (BE_MCP02_TELEMETRY01 / BE_MCP02_TELEMETRY02)
    """

    __tablename__ = "equipment_telemetry"
    __table_args__ = (Index("ix_telemetry_equipment_time", "equipment_id", "timestamp"),)

    log_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_master.equipment_id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # °C
    pressure: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 압력
    rf_power: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # kW
    gas_flow: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))  # sccm
    alarm_code: Mapped[str | None] = mapped_column(String(20))
