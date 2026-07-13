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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class EquipmentMaster(Base):
    """설비 마스터 (§8.1)"""

    __tablename__ = "equipment_masters"
    __table_args__ = (Index("ix_equipment_masters_manager_user", "manager_user_id"),)

    equipment_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    line_name: Mapped[str] = mapped_column(String(50), nullable=False)
    process_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(50))
    manager_dept: Mapped[str | None] = mapped_column(String(50))  # 책임 부서 (레거시)
    manager_name: Mapped[str | None] = mapped_column(String(50))  # 책임자 이름 (레거시 표시 폴백)
    # 책임자 유저 (BE_ADMIN01_MANAGER01), 관리자가 유저 중 지정, 유저 삭제 시 해제
    manager_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    last_inspection_at: Mapped[date | None] = mapped_column(Date)  # 마지막 점검일
    # 알람 래치 해제 기준 시각(NEW_LOOP01_LATCH01), 이 시각 이후 확정 알람만 상태에 반영
    # NULL은 해제 이력 없음(전체 이력 반영), 현장 점검·수리 시 해당 시각으로 갱신
    alarm_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 수리 힐 윈도우 래치(NEW_REPAIR01_SIM01), 이 시각 이후 heal window 동안 시뮬레이터가 정상 강제
    # 윈도우 경과 후 원래 시나리오 재개(재고장), NULL은 수리 이력 없음
    repaired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    __table_args__ = (
        Index("ix_telemetry_equipment_time", "equipment_id", "timestamp"),
        # 장비별 알람 이력 조회 부분 인덱스 (NEW_ALARM01_HISTORY01/02)
        # 알람은 전체의 1% 미만이라 alarm_code 있는 행만 인덱싱, 타임라인·코드필터·요약 모두 sub-ms
        # (equipment_id, timestamp) 인덱스는 코드필터·집계에서 풀 설비 스캔으로 저하되어 별도 필요
        Index(
            "ix_telemetry_equip_alarm_time",
            "equipment_id",
            "timestamp",
            postgresql_where=text("alarm_code IS NOT NULL"),
        ),
    )

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
