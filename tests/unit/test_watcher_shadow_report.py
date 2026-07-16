"""watcher.shadow_report 비교 로직 단위 테스트"""

from datetime import UTC, datetime, timedelta

from agentory.modules.watcher.shadow_report import Interval, compare

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _iv(eid: str, start_min: int, end_min: int) -> Interval:
    return Interval(eid, T0 + timedelta(minutes=start_min), T0 + timedelta(minutes=end_min))


def test_compare_classifies_overlap_and_exclusives():
    shadow = [_iv("A", 0, 10), _iv("B", 0, 5)]  # A는 규칙과 겹침, B는 섀도우 단독
    rule = [_iv("A", 8, 20), _iv("C", 0, 5)]  # A 겹침, C는 규칙 단독
    result = compare(shadow, rule)
    assert result == {
        "shadow_total": 2,
        "rule_total": 2,
        "detector_only": 1,  # B
        "overlapping": 1,  # A
        "rule_only": 1,  # C
    }


def test_compare_same_equipment_disjoint_time_not_overlap():
    shadow = [_iv("A", 0, 5)]
    rule = [_iv("A", 10, 20)]  # 같은 설비지만 시간 분리 → 겹침 아님
    result = compare(shadow, rule)
    assert result["detector_only"] == 1
    assert result["overlapping"] == 0
    assert result["rule_only"] == 1


def test_compare_empty():
    assert compare([], [])["detector_only"] == 0
