"""워처 이상 감지 모델 저장 (BE_ANOM01_SERVE01)

공정 유형별 적합된 PCAX 파라미터를 RDS에 보관, 서빙 로드·drift 갱신 대상
파라미터는 JSON(배열 직렬화)로 저장해 pickle 버전 취약성 회피
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
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
    train_rows: Mapped[int] = mapped_column(Integer, nullable=False)  # 적합 표본 수
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
