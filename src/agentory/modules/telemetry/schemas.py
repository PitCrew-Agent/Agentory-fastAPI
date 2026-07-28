"""텔레메트리 상태·체크리스트 응답 스키마 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StatusLevel(StrEnum):
    # 설비 상태 등급, 최신 텔레메트리 alarm_code로 판정 (양호/주의/위험, 프론트 용어 통일)
    NORMAL = "양호"
    WARNING = "주의"
    CRITICAL = "위험"


# 위험 등급 알람 코드 집합 (매뉴얼 상태 판정 정합)
# 복합 냉각 고장(ERR-402)만 위험, 다변량 이상(WRN-901)·단일 급성·드리프트·변동성은 주의
CRITICAL_ALARM_CODES: frozenset[str] = frozenset({"ERR-402"})


def alarm_severity(alarm_code: str) -> str:
    # 알람 심각도 라벨, 복합 냉각 고장(ERR-402)만 위험 나머지 주의 (다변량 WRN-901은 선제 경고)
    return StatusLevel.CRITICAL if alarm_code in CRITICAL_ALARM_CODES else StatusLevel.WARNING


class ChecklistItem(BaseModel):
    # 조치 체크리스트 단일 항목 (표시 전용, 체크 상태 미영속)
    text: str


class LineItem(BaseModel):
    # 라인 목록 항목 (라인 선택 드롭다운 + 3D 뷰 전환용)
    line_name: str = Field(description="라인명", examples=["A라인"])
    equipment_count: int = Field(description="라인 소속 설비 수", examples=[7])


class ScenePosition(BaseModel):
    # 3D 장비 좌표 (NEW_TWIN01_SCENE01), y는 현재 0
    x: float
    y: float
    z: float


class EquipmentStatusItem(BaseModel):
    # 전체 설비 상태 목록 항목 (3D 뷰 색상 매핑 + 배치 렌더용)
    equipment_id: str = Field(description="설비 id", examples=["EQP-A01"])
    line_name: str = Field(description="소속 라인명", examples=["A라인"])
    status: StatusLevel = Field(description="상태 등급 (양호·주의·위험)", examples=["양호"])
    alarm_code: str | None = Field(default=None, description="최신 알람 코드, 정상이면 null")
    # 3D 배치값 (NEW_TWIN01_SCENE01), 프론트가 위치·회전을 그대로 재현
    display_order: int | None = Field(default=None, description="라인 내 표시 순서")
    shape: str | None = Field(default=None, description="3D 모델 형태", examples=["etch"])
    bay_zone: str | None = Field(default=None, description="공정 bay 구역", examples=["north"])
    position: ScenePosition | None = Field(default=None, description="3D 좌표")
    rotation_y: float | None = Field(default=None, description="Y축 회전(radian)")


class EquipmentManager(BaseModel):
    # 설비 책임자 유저 요약 (BE_ADMIN01_MANAGER01)
    id: int = Field(description="책임자 유저 id", examples=[7])
    name: str = Field(description="책임자 이름", examples=["김억산"])
    email: str = Field(description="책임자 이메일", examples=["kim@example.com"])


class EquipmentDetail(BaseModel):
    # 선택 설비 상세 = 상태 + 메타 + 최신 센서값 + 조치 체크리스트 (대시보드 상세 패널용)
    equipment_id: str
    status: StatusLevel
    alarm_code: str | None = None
    # 알람 원인 센서 변수 키 (temperature/pressure/rf_power/gas_flow), 프론트가 해당 타일 강조
    # 양호·변동성(WRN-801) 등 특정 변수 미지정 시 빈 목록
    alarm_metrics: list[str] = []
    # 설비 메타 (equipment_masters), 설비 유형은 현재 전부 식각
    process_type: str
    manager_name: str | None = None  # 레거시 문자열 표시 폴백
    manager: EquipmentManager | None = None  # 책임자 유저, 미지정 시 None
    last_inspection_at: date | None = None
    # 최신 텔레메트리 1건, 로그 없으면 전부 None
    updated_at: datetime | None = None
    temperature: float | None = None
    pressure: float | None = None
    rf_power: float | None = None
    gas_flow: float | None = None
    # 조치 체크리스트, 양호면 빈 목록
    checklist: list[ChecklistItem] = []


class EquipmentSuggestionsResponse(BaseModel):
    # 선택 설비 상태 기반 챗봇 추천 메시지 (NEW_TWIN01_SUGGEST01), 3개, 생성 실패 시 빈 목록
    suggestions: list[str] = Field(
        default=[],
        description="설비 상태 기반 추천 질문 (보통 3개)",
        examples=[["EQP-A01 최신 온도 확인해줘", "EQP-A01 최근 알람 이력 확인해줘"]],
    )


class SensorPoint(BaseModel):
    # 시계열 그래프 한 점 (설비 센서 스냅샷, 그래프 위젯용)
    timestamp: datetime = Field(description="측정 시각")
    temperature: float | None = Field(default=None, description="온도(°C)", examples=[60.07])
    pressure: float | None = Field(default=None, description="압력(mTorr)", examples=[41.11])
    rf_power: float | None = Field(default=None, description="RF 파워(kW)", examples=[2.79])
    gas_flow: float | None = Field(default=None, description="가스 유량(sccm)", examples=[617.0])


class AlarmEventItem(BaseModel):
    # 장비별 알람 이력 타임라인 항목 (NEW_ALARM01_HISTORY01), 발생 tick 하나
    occurred_at: datetime = Field(
        description="알람 발생 시각 (ISO 8601)", examples=["2026-07-10T15:43:25+09:00"]
    )
    alarm_code: str = Field(description="알람 코드", examples=["ERR-402"])
    severity: StatusLevel = Field(description="알람 심각도 (주의·위험)", examples=["위험"])


class AlarmHistoryPage(BaseModel):
    # 장비별 알람 이력 한 페이지 (커서 기반), next_cursor로 다음 페이지 요청
    items: list[AlarmEventItem] = Field(description="이번 페이지의 알람 이력 (발생 역순)")
    next_cursor: str | None = Field(
        default=None,
        description="다음 페이지 요청 시 before에 넣을 커서, 더 없으면 null",
        examples=["MjAyNi0wNy0xMFQxNTo0MzoyNSswOTowMHwxMDI0"],
    )
    has_more: bool = Field(description="다음 페이지 존재 여부", examples=[True])


class AlarmSummaryItem(BaseModel):
    # 장비별 알람 코드 집계 항목 (NEW_ALARM01_HISTORY02), 다발순 정렬
    alarm_code: str = Field(description="알람 코드", examples=["ERR-402"])
    severity: StatusLevel = Field(description="알람 심각도 (주의·위험)", examples=["위험"])
    count: int = Field(description="기간 내 발생 횟수", examples=[42])
    first_seen: datetime = Field(
        description="최초 발생 시각", examples=["2026-07-09T06:40:18+09:00"]
    )
    last_seen: datetime = Field(
        description="최근 발생 시각", examples=["2026-07-13T02:07:15+09:00"]
    )


class AlarmSensorSummaryItem(BaseModel):
    # 센서 변수별 알람 발생 집계 (NEW_ALARM01_HISTORY02), 도넛 차트 센서별 세그먼트용
    # equipment_alarms 저널을 metric 기준 집계, 발생 횟수는 전체 합이 아닌 센서 단위 값
    metric: str = Field(description="센서 변수 키", examples=["temperature"])
    count: int = Field(description="기간 내 해당 센서 알람 발생 횟수", examples=[12])
    first_seen: datetime = Field(
        description="최초 발생 시각", examples=["2026-07-09T06:40:18+09:00"]
    )
    last_seen: datetime = Field(
        description="최근 발생 시각", examples=["2026-07-13T02:07:15+09:00"]
    )
