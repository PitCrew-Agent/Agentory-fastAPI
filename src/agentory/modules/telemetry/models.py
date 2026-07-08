"""
정형 RDB 모델 (DEV_DATABASE)
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class EquipmentMaster(Base):
    """설비 마스터 (§8.1)"""

    __tablename__ = "equipment_masters"

    equipment_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    line_name: Mapped[str] = mapped_column(String(50), nullable=False)
    process_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(50))
    manager_dept: Mapped[str | None] = mapped_column(String(50))  # 책임 부서
    manager_name: Mapped[str | None] = mapped_column(String(50))  # 책임자 이름
    last_inspection_at: Mapped[date | None] = mapped_column(Date)  # 마지막 점검일

    # 3D 트윈 뷰 배치값 (NEW_TWIN01_SCENE01), 프론트가 라인·설비 위치를 그대로 재현
    display_order: Mapped[int | None] = mapped_column(Integer)  # 라인 내 표시 순서
    shape: Mapped[str | None] = mapped_column(String(30))  # 3D 모델 형태, 식각은 etch
    bay_zone: Mapped[str | None] = mapped_column(String(10))  # 공정 bay 구역, north 또는 south
    position_x: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))  # 3D X 좌표
    position_y: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))  # 3D Y 좌표, 현재는 0
    position_z: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))  # 3D Z 좌표
    rotation_y: Mapped[Decimal | None] = mapped_column(Numeric(17, 15))  # Y축 회전 radian


class EquipmentTelemetry(Base):
    """설비 로그·센서 (§8.2)

    - (equipment_id, timestamp) 복합 인덱스로 설비별 기간 조회 대응
    - (BE_MCP02_TELEMETRY01 / BE_MCP02_TELEMETRY02)
    """

    __tablename__ = "equipment_telemetries"
    __table_args__ = (Index("ix_telemetry_equipment_time", "equipment_id", "timestamp"),)

    log_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_masters.equipment_id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # °C
    pressure: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 압력
    rf_power: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # kW
    gas_flow: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))  # sccm
    alarm_code: Mapped[str | None] = mapped_column(String(20))
