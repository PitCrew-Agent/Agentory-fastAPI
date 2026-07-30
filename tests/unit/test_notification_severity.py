"""알림 severity 판정 단위 테스트

severity는 telemetry.alarm_severity 단일 소스에 정합 (복합 냉각 ERR-402만 위험)
"""

from agentory.modules.notification.repository import _severity
from agentory.modules.telemetry.schemas import StatusLevel


def test_severity_only_err402_is_critical():
    assert _severity("ERR-402") == StatusLevel.CRITICAL


def test_severity_single_acute_and_warn_are_warning():
    # 단일 급성(ERR-401·ERR-301·ERR-201)과 다변량 WRN-901은 주의, 접두만으로 재추론 금지
    assert _severity("ERR-401") == StatusLevel.WARNING
    assert _severity("ERR-301") == StatusLevel.WARNING
    assert _severity("ERR-201") == StatusLevel.WARNING
    assert _severity("WRN-901") == StatusLevel.WARNING
