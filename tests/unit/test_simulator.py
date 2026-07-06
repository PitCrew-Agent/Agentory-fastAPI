"""시뮬레이터 센서 생성·판정 로직 단위 테스트 (BE_SIM01_GEN01)

DB 없이 순수 생성 로직을 결정론적(seed 고정)으로 검증
SPC 동적 밴드 모델의 정상·급성(ERR-402)·드리프트(WRN-70x)·변동성(WRN-801)·다변량(ERR-901) 판정 확인
(참고서 §3·§5·§6·§7)
"""

import random
from collections import deque
from decimal import Decimal

from simulator.generator import ERR402, generate_reading
from simulator.scenarios import NORMAL, SCENARIOS


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
