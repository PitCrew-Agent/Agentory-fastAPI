"""상태 판정·체크리스트 매핑 단위 테스트 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)"""

import pytest

from agentory.modules.telemetry.checklists import (
    CHECKLIST_TEMPLATES,
    COMMON_ITEMS,
    build_checklist_items,
)
from agentory.modules.telemetry.schemas import StatusLevel
from agentory.modules.telemetry.service import assess_status


@pytest.mark.parametrize(
    "alarm_code, expected",
    [
        (None, StatusLevel.NORMAL),
        ("", StatusLevel.NORMAL),
        ("WRN-701", StatusLevel.WARNING),
        ("WRN-801", StatusLevel.WARNING),
        ("ERR-402", StatusLevel.CRITICAL),
        ("ERR-901", StatusLevel.CRITICAL),
    ],
)
def test_assess_status_by_alarm_prefix(alarm_code, expected):
    assert assess_status(alarm_code) == expected


def test_assess_status_unknown_code_defaults_to_warning():
    # 미분류 알람도 보수적으로 경고 처리
    assert assess_status("XYZ-000") == StatusLevel.WARNING


def test_checklist_empty_when_normal():
    # 정상(알람 없음)은 빈 체크리스트
    assert build_checklist_items(None) == []
    assert build_checklist_items("") == []


def test_checklist_err402_has_specific_and_common_tail():
    items = build_checklist_items("ERR-402")
    # 냉각 급성 특화 항목 포함
    assert "라인 정지 없이 압력 밸브 상태 확인" in items
    # 공통 마무리 항목이 항상 끝에 부착
    assert items[-len(COMMON_ITEMS) :] == COMMON_ITEMS


def test_checklist_drift_has_recalibration_item():
    items = build_checklist_items("WRN-701")
    assert "온도 센서 기준값 재측정" in items


def test_checklist_unknown_code_has_common_only():
    # 등록되지 않은 알람 코드는 공통 항목만 노출
    assert build_checklist_items("WRN-999") == COMMON_ITEMS


def test_all_known_codes_have_nonempty_template():
    # 등록 코드 전부 특화 항목 보유
    for code, items in CHECKLIST_TEMPLATES.items():
        assert items, f"{code} 템플릿 비어 있음"
