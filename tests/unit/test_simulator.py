"""시뮬레이터 센서 생성·판정 로직 단위 테스트 (BE_SIM01_GEN01)

DB 없이 순수 생성 로직을 결정론적(seed 고정)으로 검증
SPC 동적 밴드 모델의 정상·급성(ERR-402)·드리프트(WRN-70x) 판정을 확인 (참고서 §3·§7)
"""

import random
from decimal import Decimal

from simulator.generator import ERR402, generate_reading
from simulator.scenarios import NORMAL, SCENARIOS


def _rng() -> random.Random:
    return random.Random(42)


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
