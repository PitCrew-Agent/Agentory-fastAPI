"""시뮬레이터 센서 생성·판정 로직 단위 테스트 (BE_SIM01_GEN01)

DB 없이 순수 생성 로직을 결정론적(seed 고정)으로 검증
변수별 독립 판정(급성 ERR-40x·드리프트 WRN-70x·변동성 WRN-801)과 대표값(worst-of) 확인
한 설비가 변수마다 다른 상태를 동시에 갖는 per-variable 모델 검증 (참고서 §3·§5·§6·§7)
"""

import random
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from simulator.generator import VARS, generate_reading
from simulator.main import _select_spec
from simulator.scenarios import NORMAL, PRESETS, SCENARIOS


def _rng() -> random.Random:
    return random.Random(42)


def _run_scenario(name: str, *, seed: int = 7, start: int = 3, ticks: int = 40) -> set[str]:
    # main과 동일하게 최근값 창(history) + 연속 2 tick 지속 게이트를 적용해 확정 대표 알람 집합 반환
    rng = random.Random(seed)
    scenario = SCENARIOS[name]
    prev = None
    window: deque = deque(maxlen=8)
    confirmed: set[str] = set()
    for tick in range(ticks):
        reading = generate_reading(
            "EQP-002",
            "Etching",
            scenario=scenario,
            tick=tick,
            drift_start_tick=start,
            history=list(window),
            rng=rng,
        )
        window.append(reading)
        if reading.alarm_code and reading.alarm_code == prev:
            confirmed.add(reading.alarm_code)
        prev = reading.alarm_code
    return confirmed


def test_normal_reading_has_no_alarm():
    reading = generate_reading("EQP-002", "Etching", scenario=NORMAL, tick=0, rng=_rng())
    assert reading.alarm_code is None
    assert all(reading.alarm_codes[var] is None for var in VARS)
    assert reading.equipment_id == "EQP-002"
    # Etching 온도 중심값(60.0) 주변
    assert Decimal("59") < reading.temperature < Decimal("61")


def test_reading_values_have_two_decimal_places():
    reading = generate_reading("EQP-002", "Etching", scenario=NORMAL, tick=0, rng=_rng())
    for value in (reading.temperature, reading.pressure, reading.rf_power, reading.gas_flow):
        assert value.as_tuple().exponent == -2


def test_normal_variable_no_false_drift_at_high_tick():
    # 드리프트 없는 정상 시나리오는 tick이 커져도 밴드가 고정이라 알람 없음
    reading = generate_reading(
        "EQP-002", "Etching", scenario=NORMAL, tick=100, drift_start_tick=0, rng=_rng()
    )
    assert reading.alarm_code is None


def test_pressure_drift_reaches_wrn702():
    # 압력 중심선이 상승해 밴드가 하드리밋 회랑을 소진하면 WRN-702 후보 발생 (참고서 §3.3)
    scenario = SCENARIOS["pressure_drift_pm"]
    reading = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=25, drift_start_tick=0, rng=_rng()
    )
    assert reading.alarm_code == "WRN-702"
    assert reading.alarm_codes["pressure"] == "WRN-702"
    # 다른 변수는 정상 (변수별 독립)
    assert reading.alarm_codes["temperature"] is None


def test_concurrent_per_variable_alarms():
    # 한 설비가 온도 급성(ERR-401) + 압력 드리프트(WRN-702)를 동시에 독립적으로 가짐
    scenario = SCENARIOS["temp_acute_pressure_drift"]
    reading = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=30, drift_start_tick=0, rng=_rng()
    )
    assert reading.alarm_codes["temperature"] == "ERR-401"
    assert reading.alarm_codes["pressure"] == "WRN-702"
    assert reading.alarm_codes["rf_power"] is None
    assert reading.alarm_codes["gas_flow"] is None
    # 대표값은 변수별 중 최고 심각도(worst-of) → ERR-401
    assert reading.alarm_code == "ERR-401"


def test_variance_increase_triggers_wrn801():
    # 변동성 증가 시나리오는 WRN-801을 확정 발생시킴 (참고서 §6)
    assert "WRN-801" in _run_scenario("variance_increase")


def test_drift_scenarios_do_not_false_trigger_variance():
    # 드리프트는 중심선 이동일 뿐이라 변동성(WRN-801) 오탐이 없어야 함 (1차 차분 판정)
    for name in ("pressure_drift_pm", "gas_flow_drift_pm"):
        assert "WRN-801" not in _run_scenario(name)


def test_gain_accelerates_pm_drift():
    # gain 배수는 PM 드리프트를 가속해 같은 tick에서 더 이른 WRN 진입·더 큰 변위 유도
    scenario = SCENARIOS["pressure_drift_pm"]
    early = 6
    base = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=early, drift_start_tick=0, rng=_rng()
    )
    boosted = generate_reading(
        "EQP-002",
        "Etching",
        scenario=scenario,
        tick=early,
        drift_start_tick=0,
        gain=5.0,
        rng=_rng(),
    )
    assert base.alarm_code is None  # gain 1.0은 아직 회랑 미소진
    assert boosted.alarm_code == "WRN-702"  # gain 5.0은 조기 진입
    assert boosted.pressure > base.pressure


def test_gain_default_keeps_baseline_behavior():
    # gain 미지정은 1.0 기본이라 기존 판정과 동일
    scenario = SCENARIOS["pressure_drift_pm"]
    default = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=25, drift_start_tick=0, rng=_rng()
    )
    explicit = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=25, drift_start_tick=0, gain=1.0, rng=_rng()
    )
    assert default.alarm_code == explicit.alarm_code == "WRN-702"
    assert default.pressure == explicit.pressure


def test_floor_demo_preset_covers_all_sensor_variables():
    # 데모 preset은 4개 센서 변수를 급성으로 모두 노출, 복합 코드 없이 변수별 단일 이상만 배치
    assigned = [SCENARIOS[name] for name in PRESETS["floor_demo"].values()]
    acute = {var for s in assigned for var in s.acute_vars}
    assert acute == set(VARS)
    # 최소 한 설비는 급성+드리프트를 서로 다른 변수에 동시 배치 (변수별 동시 독립 상태)
    assert any(s.acute_vars and s.drift_vars for s in assigned)
    # 한 변수가 급성과 드리프트를 겹쳐 갖지 않음 (변수 내 상태 모호 방지)
    assert all(not (set(s.acute_vars) & set(s.drift_vars)) for s in assigned)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("temperature_acute", "ERR-401"),
        ("pressure_acute", "ERR-301"),
        ("rf_power_acute", "ERR-201"),
        ("gas_flow_acute", "WRN-501"),
    ],
)
def test_acute_scenarios_trigger_single_variable_alarm(name, expected):
    # 급성 단일변수 시나리오는 해당 변수 밴드 이탈 알람을 확정 발생시킴 (참고서 §4)
    assert expected in _run_scenario(name)


def test_acute_offset_keeps_value_within_fault_clamp():
    # 급성 오프셋 값도 하드리밋 바깥 현실 범위를 넘지 않아야 함
    reading = generate_reading(
        "EQP-002",
        "Etching",
        scenario=SCENARIOS["gas_flow_acute"],
        tick=10,
        drift_start_tick=0,
        rng=_rng(),
    )
    # 가스유량 USL 660 초과이나 클램프 상한 720 이내
    assert 660.0 < float(reading.gas_flow) <= 720.0


# --- 수리 힐 윈도우 시나리오 결정 (NEW_REPAIR01_SIM01) ---

_NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
_HEAL = timedelta(minutes=60)
_MAP = {"EQP-A05": "temp_acute_pressure_drift"}  # preset 배치상 고장 설비


def test_heal_window_forces_normal_within_window():
    # 수리 직후(윈도우 내)면 preset이 고장이어도 정상 강제
    spec = _select_spec(
        "EQP-A05",
        _NOW - timedelta(minutes=10),
        now=_NOW,
        heal_window=_HEAL,
        scenario_map=_MAP,
        scenario=NORMAL,
        target_equipment_id="EQP-003",
    )
    assert spec is NORMAL


def test_heal_window_resumes_scenario_after_window():
    # 힐 윈도우 경과(1시간 초과)면 원래 preset 고장 시나리오 재개(재고장)
    spec = _select_spec(
        "EQP-A05",
        _NOW - timedelta(minutes=61),
        now=_NOW,
        heal_window=_HEAL,
        scenario_map=_MAP,
        scenario=NORMAL,
        target_equipment_id="EQP-003",
    )
    assert spec is SCENARIOS["temp_acute_pressure_drift"]


def test_no_repair_keeps_scenario():
    # 수리 이력 없으면(repaired_at None) preset 그대로
    spec = _select_spec(
        "EQP-A05",
        None,
        now=_NOW,
        heal_window=_HEAL,
        scenario_map=_MAP,
        scenario=NORMAL,
        target_equipment_id="EQP-003",
    )
    assert spec is SCENARIOS["temp_acute_pressure_drift"]


def test_heal_window_target_mode_after_window():
    # preset 미사용(target 모드)에서도 윈도우 경과 후 target 설비는 시나리오 재개
    spec = _select_spec(
        "EQP-003",
        _NOW - timedelta(minutes=90),
        now=_NOW,
        heal_window=_HEAL,
        scenario_map=None,
        scenario=SCENARIOS["temperature_acute"],
        target_equipment_id="EQP-003",
    )
    assert spec is SCENARIOS["temperature_acute"]
