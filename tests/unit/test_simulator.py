"""시뮬레이터 센서 생성·판정 로직 단위 테스트 (BE_SIM01_GEN01)

DB 없이 순수 생성 로직을 결정론적(seed 고정)으로 검증
SPC 동적 밴드 모델의 정상·급성(ERR-402)·드리프트(WRN-70x)·변동성(WRN-801)·다변량(ERR-901) 판정 확인
(참고서 §3·§5·§6·§7)
"""

import random
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from simulator.generator import ERR402, generate_reading
from simulator.main import _select_spec
from simulator.scenarios import NORMAL, PRESETS, SCENARIOS


def _rng() -> random.Random:
    return random.Random(42)


def _run_scenario(name: str, *, seed: int = 7, start: int = 3, ticks: int = 40) -> set[str]:
    # main과 동일하게 최근값 창(history) + 연속 2 tick 지속 게이트를 적용해 확정 알람 집합 반환
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


def test_err402_scenario_triggers_err402():
    # 온도 급상승(USL 초과) + 압력 하강이 겹치면 ERR-402가 최우선으로 선정
    scenario = SCENARIOS["err402_temp_rise"]
    reading = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=5, drift_start_tick=0, rng=_rng()
    )
    assert reading.alarm_code == ERR402
    assert float(reading.temperature) >= 61.50


def test_variance_increase_triggers_wrn801():
    # 변동성 증가 시나리오는 WRN-801을 확정 발생시킴 (참고서 §6)
    assert "WRN-801" in _run_scenario("variance_increase")


def test_multivariate_anomaly_triggers_err901():
    # rf 상승 + 온도 하강 관계 붕괴 시나리오는 ERR-901을 확정 발생시킴 (참고서 §6)
    assert "ERR-901" in _run_scenario("multivariate_anomaly")


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


def test_gain_excludes_err402_and_multivariate():
    # err402·다변량 레이트는 gain 미적용이라 온도 변위가 gain에 불변
    scenario = SCENARIOS["err402_temp_rise"]
    g1 = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=5, drift_start_tick=0, rng=_rng()
    )
    g5 = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=5, drift_start_tick=0, gain=5.0, rng=_rng()
    )
    assert g1.temperature == g5.temperature


def test_floor_demo_preset_covers_alarm_families():
    # 데모 preset은 급성 이상(ERR-402) 1종 + 서로 다른 센서 드리프트 경고 포함
    assigned = [SCENARIOS[name] for name in PRESETS["floor_demo"].values()]
    assert any(s.err402 for s in assigned)
    drift_vars = {v for s in assigned for v in s.drift_vars}
    assert drift_vars == {"temperature", "pressure", "gas_flow"}


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


def test_err402_values_stay_in_physical_range_over_long_run():
    # 장시간 누적돼도 온도·압력이 하드리밋 밖 현실 범위(클램프) 안에 머물러야 함
    # 회귀 대상: 클램프 없으면 온도 540·압력 -999 같은 물리적으로 불가능한 발산 발생
    scenario = SCENARIOS["err402_temp_rise"]
    reading = generate_reading(
        "EQP-002", "Etching", scenario=scenario, tick=600, drift_start_tick=0, rng=_rng()
    )
    # 온도 USL 61.5 + 0.5*span(3.0)=63.0, 압력 LSL 30 - 0.5*span(25)=17.5 (밴드 잡음 여유 포함)
    assert 61.5 <= float(reading.temperature) <= 64.0
    assert 16.0 <= float(reading.pressure) <= 40.0
    assert reading.alarm_code == ERR402


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
_MAP = {"EQP-A05": "err402_temp_rise"}  # preset 배치상 고장 설비


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
    assert spec is SCENARIOS["err402_temp_rise"]


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
    assert spec is SCENARIOS["err402_temp_rise"]


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
