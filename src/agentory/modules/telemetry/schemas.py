"""텔레메트리 상태·체크리스트 응답 스키마 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


class StatusLevel(StrEnum):
    # 설비 상태 등급, 최신 텔레메트리 alarm_code로 판정 (양호/주의/위험, 프론트 용어 통일)
    NORMAL = "양호"
    WARNING = "주의"
    CRITICAL = "위험"


class ChecklistItem(BaseModel):
    # 조치 체크리스트 단일 항목 (표시 전용, 체크 상태 미영속)
    text: str


class LineItem(BaseModel):
    # 라인 목록 항목 (라인 선택 드롭다운 + 3D 뷰 전환용)
    line_name: str
    equipment_count: int


class EquipmentStatusItem(BaseModel):
    # 전체 설비 상태 목록 항목 (3D 뷰 색상 매핑용)
    equipment_id: str
    status: StatusLevel
    alarm_code: str | None = None


class EquipmentDetail(BaseModel):
    # 선택 설비 상세 = 상태 + 메타 + 최신 센서값 + 조치 체크리스트 (대시보드 상세 패널용)
    equipment_id: str
    status: StatusLevel
    alarm_code: str | None = None
    # 설비 메타 (equipment_masters), 설비 유형은 현재 전부 식각
    process_type: str
    manager_name: str | None = None
    last_inspection_at: date | None = None
    # 최신 텔레메트리 1건, 로그 없으면 전부 None
    updated_at: datetime | None = None
    temperature: float | None = None
    pressure: float | None = None
    rf_power: float | None = None
    gas_flow: float | None = None
    # 조치 체크리스트, 양호면 빈 목록
    checklist: list[ChecklistItem] = []


class SensorPoint(BaseModel):
    # 시계열 그래프 한 점 (설비 센서 스냅샷, 그래프 위젯용)
    timestamp: datetime
    temperature: float | None = None
    pressure: float | None = None
    rf_power: float | None = None
    gas_flow: float | None = None
