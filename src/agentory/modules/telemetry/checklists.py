"""알람 코드별 조치 체크리스트·원인 변수 매핑 (NEW_LOOP01_CHECK01 / NEW_TWIN01_SYNC01)

경고·이상 설비 선택 시 노출할 점검·조치 항목을 알람 코드별로 매핑
급성 밴드 이탈은 해당 센서 실측·계통 점검, 드리프트는 기준값 재측정·예방정비,
복합 이상은 관련 계통 상관 점검으로 구성
마지막 공통 항목은 작업 로그 등록과 재발 시 재점검 안내
원인 센서 변수는 프론트 상세 타일 강조용으로 코드별 매핑
"""

from agentory.common.context import get_locale
from agentory.common.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES

# 로케일별 알람 코드 특화 조치 항목
CHECKLIST_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "ko": {
        # 급성 밴드 이탈 (센서별 즉시 점검)
        "ERR-401": ["온도 센서 실측값 확인", "냉각·가열 계통 이상 여부 점검"],
        "ERR-301": ["압력 센서 실측값 확인", "압력 밸브·배관 누설 점검"],
        "ERR-201": ["RF 파워 출력 실측값 확인", "RF 매칭·전원부 상태 점검"],
        "WRN-501": ["가스 유량(MFC) 실측값 확인", "가스 라인·밸브 개폐 상태 점검"],
        # 드리프트·예방정비 (기준값 재측정)
        "WRN-701": ["온도 센서 기준값 재측정", "온도 계통 예방정비(PM) 일정 확인"],
        "WRN-702": ["압력 센서 기준값 재측정", "압력 계통 예방정비(PM) 일정 확인"],
        "WRN-703": ["RF 파워 기준값 재측정", "RF 계통 예방정비(PM) 일정 확인"],
        "WRN-704": ["가스 유량 기준값 재측정", "가스 계통 예방정비(PM) 일정 확인"],
        # 냉각 급성 (온도 상승 + 압력 하강)
        "ERR-402": ["라인 정지 없이 압력 밸브 상태 확인", "냉각수 유량·온도 점검"],
        # 변동성 증가
        "WRN-801": ["센서 신호 노이즈·접점 확인", "주변 진동·간섭원 점검"],
        # 다변량 관계 붕괴
        "ERR-901": ["RF 파워·온도 상관 점검", "공정 레시피 파라미터 확인"],
    },
    "en": {
        "ERR-401": ["Verify temperature sensor reading", "Inspect cooling/heating system"],
        "ERR-301": ["Verify pressure sensor reading", "Inspect pressure valve/pipe leakage"],
        "ERR-201": ["Verify RF power output reading", "Inspect RF matching/power supply"],
        "WRN-501": ["Verify gas flow (MFC) reading", "Inspect gas line/valve open-close state"],
        "WRN-701": [
            "Re-measure temperature sensor baseline",
            "Check temperature system PM schedule",
        ],
        "WRN-702": ["Re-measure pressure sensor baseline", "Check pressure system PM schedule"],
        "WRN-703": ["Re-measure RF power baseline", "Check RF system PM schedule"],
        "WRN-704": ["Re-measure gas flow baseline", "Check gas system PM schedule"],
        "ERR-402": [
            "Check pressure valve state without stopping the line",
            "Inspect coolant flow/temperature",
        ],
        "WRN-801": ["Check sensor signal noise/contacts", "Inspect nearby vibration/interference"],
        "ERR-901": ["Check RF power/temperature correlation", "Verify process recipe parameters"],
    },
}

# 로케일별 공통 마무리 항목, 작업 로그 등록과 재발 시 재점검 안내
COMMON_ITEMS: dict[str, list[str]] = {
    "ko": [
        "점검 결과를 작업 로그에 등록",
        "동일 알림 재발 시 재점검 실행",
    ],
    "en": [
        "Register inspection result in work log",
        "Re-check if the same alert recurs",
    ],
}

# 알람 코드별 원인 센서 변수 키 (SensorPoint·EquipmentDetail 필드명과 동일, 프론트 타일 강조용)
# 급성·드리프트는 단일 변수, 복합 이상은 관련 변수 다수, 변동성(WRN-801)은 특정 변수 미지정
ALARM_METRICS: dict[str, list[str]] = {
    "ERR-401": ["temperature"],
    "ERR-301": ["pressure"],
    "ERR-201": ["rf_power"],
    "WRN-501": ["gas_flow"],
    "WRN-701": ["temperature"],
    "WRN-702": ["pressure"],
    "WRN-703": ["rf_power"],
    "WRN-704": ["gas_flow"],
    "ERR-402": ["temperature", "pressure"],
    "ERR-901": ["rf_power", "temperature"],
    "WRN-801": [],
}


def build_checklist_items(alarm_code: str | None, locale: str | None = None) -> list[str]:
    # 정상(알람 없음)은 빈 목록, 알람이 있으면 특화 항목 + 공통 항목, 로케일별 지역화
    if not alarm_code:
        return []
    loc = locale or get_locale()
    if loc not in SUPPORTED_LOCALES:
        loc = DEFAULT_LOCALE
    # 미등록 코드는 특화 항목 없이 공통 항목만 노출
    specific = CHECKLIST_TEMPLATES[loc].get(alarm_code, [])
    return [*specific, *COMMON_ITEMS[loc]]


def alarm_metrics(alarm_code: str | None) -> list[str]:
    # 알람 원인 센서 변수 키 목록, 정상·미등록·미지정(WRN-801) 코드는 빈 목록
    if not alarm_code:
        return []
    return ALARM_METRICS.get(alarm_code, [])
