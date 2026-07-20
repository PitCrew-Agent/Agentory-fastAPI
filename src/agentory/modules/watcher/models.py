"""워처 이상 감지 모델 저장 (BE_ANOM01_SERVE01)

공정 유형별 적합된 PCAX 파라미터를 RDS에 보관, 서빙 로드·drift 갱신 대상
파라미터는 JSON(배열 직렬화)로 저장해 pickle 버전 취약성 회피
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentory.core.db import Base


class EquipmentAnomalyModel(Base):
    """공정 유형별 이상 감지 모델 파라미터 (anomaly.models.PcaMspc.to_state 산출물)"""

    __tablename__ = "equipment_anomaly_models"

    process_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, nullable=False)  # 윈도우 길이 (tick)
    stride: Mapped[int] = mapped_column(Integer, nullable=False)  # 슬라이드 간격 (tick)
    confirm_k: Mapped[int] = mapped_column(Integer, nullable=False)  # 지속 확인 윈도우 수
    ewma_limit: Mapped[float | None] = mapped_column(nullable=True)  # EWMA 한계 (재캘리브레이션)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)  # PcaMspc.to_state()
    # VAR 보완 경로 파라미터 (BE_ANOM01_VAR01), NULL이면 VAR 경로 미사용
    var_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    var_ewma_limit: Mapped[float | None] = mapped_column(nullable=True)  # VAR 경로 EWMA 한계
    train_rows: Mapped[int] = mapped_column(Integer, nullable=False)  # 적합 표본 수
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EquipmentAnomalyShadowEvent(Base):
    """섀도우 모드 이상 감지 관찰 저널 (BE_ANOM01_SHADOW01)

    스코어러 판정을 EquipmentAlarm과 동일한 발생/해제 구조로 기록하되 알림 파이프라인
    미연결, 규칙 레이어 알람과 조인해 감지 격차를 정량 리뷰
    """

    __tablename__ = "equipment_anomaly_shadow_events"
    __table_args__ = (
        # 활성 섀도우 관찰 조회 부분 인덱스, 발생/해제 전이 판정용
        Index(
            "ix_anomaly_shadow_active",
            "equipment_id",
            postgresql_where=text("cleared_at IS NULL"),
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_masters.equipment_id"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(20), nullable=False)  # 최대 기여 센서
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 발생 시 정규화 점수
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL이면 활성


class EquipmentAnomalyCalibration(Base):
    """설비별 EWMA 한계 캘리브레이션 (BE_ANOM01_CALIB01)

    공유 공정 유형 모델에 대해 설비 개체 정상 분포로 산출한 한계, 개체 오프셋 흡수
    미보유 설비는 스코어러가 공정 유형 한계로 폴백
    """

    __tablename__ = "equipment_anomaly_calibrations"

    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_masters.equipment_id"), primary_key=True
    )
    ewma_limit: Mapped[float] = mapped_column(Float, nullable=False)  # 설비별 EWMA 한계
    train_windows: Mapped[int] = mapped_column(Integer, nullable=False)  # 캘리브 표본 수
    calibrated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
