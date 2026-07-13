"""알림 메시지 템플릿 단위 테스트 (NEW_PROACT01_ALERT01)"""

from agentory.modules.notification.messages import ALARM_MESSAGES, build_notification_message

# 시뮬레이터·체크리스트가 실제 생성하는 알람 코드
SIMULATOR_CODES = {
    "ERR-201",
    "ERR-301",
    "ERR-401",
    "ERR-402",
    "ERR-901",
    "WRN-501",
    "WRN-701",
    "WRN-702",
    "WRN-703",
    "WRN-704",
    "WRN-801",
}


def test_message_known_code_combines_equipment_and_detail():
    msg = build_notification_message("EQP-003", "ERR-402")
    assert msg.startswith("EQP-003")
    assert "냉각 이상" in msg


def test_message_unknown_code_falls_back_to_code():
    # 미등록 코드는 코드 원문 노출
    msg = build_notification_message("EQP-009", "ZZZ-000")
    assert "EQP-009" in msg
    assert "ZZZ-000" in msg


def test_message_localized_to_english():
    # locale=en이면 영어 메시지
    msg = build_notification_message("EQP-003", "ERR-402", locale="en")
    assert msg.startswith("EQP-003")
    assert "Cooling anomaly" in msg


def test_all_simulator_codes_have_message():
    # 실제 알람 코드 전부 메시지 보유 (ko·en 모두)
    assert SIMULATOR_CODES <= set(ALARM_MESSAGES["ko"])
    assert SIMULATOR_CODES <= set(ALARM_MESSAGES["en"])
