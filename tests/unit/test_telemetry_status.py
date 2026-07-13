"""상태 판정·체크리스트 매핑 단위 테스트 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)"""

import pytest

from agentory.modules.telemetry.checklists import (
    ALARM_METRICS,
    CHECKLIST_TEMPLATES,
    COMMON_ITEMS,
    alarm_metrics,
    build_checklist_items,
)
from agentory.modules.telemetry.schemas import SensorPoint, StatusLevel
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
    # 로케일 미지정은 기본 ko
    items = build_checklist_items("ERR-402")
    # 냉각 급성 특화 항목 포함
    assert "라인 정지 없이 압력 밸브 상태 확인" in items
    # 공통 마무리 항목이 항상 끝에 부착
    assert items[-len(COMMON_ITEMS["ko"]) :] == COMMON_ITEMS["ko"]


def test_checklist_localized_to_english():
    # locale=en이면 영어 체크리스트
    items = build_checklist_items("ERR-402", locale="en")
    assert "Inspect coolant flow/temperature" in items
    assert items[-len(COMMON_ITEMS["en"]) :] == COMMON_ITEMS["en"]


def test_checklist_drift_has_recalibration_item():
    items = build_checklist_items("WRN-701")
    assert "온도 센서 기준값 재측정" in items


def test_checklist_unknown_code_has_common_only():
    # 등록되지 않은 알람 코드는 공통 항목만 노출 (기본 ko)
    assert build_checklist_items("WRN-999") == COMMON_ITEMS["ko"]


def test_all_known_codes_have_nonempty_template():
    # 등록 코드 전부 특화 항목 보유 (ko·en 모두)
    for locale, table in CHECKLIST_TEMPLATES.items():
        for code, items in table.items():
            assert items, f"{locale}/{code} 템플릿 비어 있음"


@pytest.mark.parametrize(
    "alarm_code, expected",
    [
        (None, []),
        ("", []),
        ("ERR-401", ["temperature"]),
        ("WRN-702", ["pressure"]),
        ("ERR-402", ["temperature", "pressure"]),
        ("ERR-901", ["rf_power", "temperature"]),
        ("WRN-801", []),  # 변동성은 특정 변수 미지정
        ("XYZ-000", []),  # 미등록 코드
    ],
)
def test_alarm_metrics_by_code(alarm_code, expected):
    assert alarm_metrics(alarm_code) == expected


def test_alarm_metrics_keys_match_sensor_fields():
    # 원인 변수 키가 실제 센서 필드명과 일치해야 프론트 타일 매핑 가능
    sensor_fields = set(SensorPoint.model_fields) - {"timestamp"}
    for code, metrics in ALARM_METRICS.items():
        for metric in metrics:
            assert metric in sensor_fields, f"{code} 원인 변수 {metric} 센서 필드 아님"
