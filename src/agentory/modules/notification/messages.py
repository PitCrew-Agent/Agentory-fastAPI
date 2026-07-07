"""알람 코드별 알림 메시지 템플릿 (NEW_PROACT01_ALERT01)

실제 시뮬레이터·체크리스트가 쓰는 알람 코드 기준, 목업 임의 코드는 제외
알림 이력·SSE 스트림에서 공통 사용
"""

# 알람 코드별 사람이 읽는 메시지 조각
ALARM_MESSAGES: dict[str, str] = {
    # 급성 밴드 이탈 (센서별 위험)
    "ERR-401": "온도 위험 기준 초과",
    "ERR-301": "압력 위험 기준 초과",
    "ERR-201": "RF 파워 위험 기준 초과",
    "WRN-501": "가스 유량 경고 기준 이탈",
    # 드리프트 (기준값 이탈 추세)
    "WRN-701": "온도 드리프트 감지",
    "WRN-702": "압력 드리프트 감지",
    "WRN-703": "RF 파워 드리프트 감지",
    "WRN-704": "가스 유량 드리프트 감지",
    # 냉각 급성 (온도 상승 + 압력 하강)
    "ERR-402": "냉각 이상 (온도 상승·압력 하강)",
    # 변동성 증가
    "WRN-801": "센서 변동성 증가",
    # 다변량 관계 붕괴
    "ERR-901": "다변량 이상 (센서 상관 붕괴)",
}


def build_notification_message(equipment_id: str, alarm_code: str) -> str:
    # 설비 + 코드 의미 결합, 미등록 코드는 코드 원문 노출
    detail = ALARM_MESSAGES.get(alarm_code, f"{alarm_code} 알람 발생")
    return f"{equipment_id} {detail}"
