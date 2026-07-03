"""시뮬레이터 센서 생성 로직 단위 테스트 (BE_SIM01_GEN01)

DB 없이 순수 생성 로직을 결정론적(seed 고정)으로 검증
이상 모드가 무한 상승이 아니라 평형점에 점근·정착하는지 확인
"""

import random
from decimal import Decimal

from simulator.generator import (
    ANOMALY_TEMP_PLATEAU,
    ERR402,
    ERR402_TEMP_THRESHOLD,
    MIN_PRESSURE,
    generate_reading,
)


def _rng() -> random.Random:
    return random.Random(42)


def test_normal_reading_has_no_alarm():
    reading = generate_reading("EQP-001", "Etching", rng=_rng())
    assert reading.alarm_code is None
    assert reading.equipment_id == "EQP-001"
    # baseline(42.0) 주변 값
    assert Decimal("38") < reading.temperature < Decimal("46")


def test_reading_values_have_two_decimal_places():
    reading = generate_reading("EQP-001", "Etching", rng=_rng())
    for value in (reading.temperature, reading.pressure, reading.rf_power, reading.gas_flow):
        assert value.as_tuple().exponent == -2


def test_anomaly_rises_then_plateaus():
    """초반 상승 후 평형점 부근 정착, 무한 상승하지 않음"""
    r0 = generate_reading("EQP-003", "Etching", anomaly=True, step=0, rng=_rng())
    r6 = generate_reading("EQP-003", "Etching", anomaly=True, step=6, rng=_rng())
    r100 = generate_reading("EQP-003", "Etching", anomaly=True, step=100, rng=_rng())

    # 상승 확인
    assert r6.temperature > r0.temperature
    # 평형점 부근 도달, 상한 존재 (노이즈 여유 포함)
    plateau = Decimal(str(ANOMALY_TEMP_PLATEAU))
    assert plateau - Decimal("3") <= r100.temperature <= plateau + Decimal("2")
    # step 6과 step 100의 온도 차이가 작음 (이미 정착)
    assert abs(r100.temperature - r6.temperature) < Decimal("4")


def test_anomaly_injects_err402_above_threshold():
    # step 0은 baseline(42) 정상, 온도 상승 후 ERR-402 발생
    r0 = generate_reading("EQP-003", "Etching", anomaly=True, step=0, rng=_rng())
    r6 = generate_reading("EQP-003", "Etching", anomaly=True, step=6, rng=_rng())
    assert r0.alarm_code is None
    assert float(r6.temperature) >= ERR402_TEMP_THRESHOLD
    assert r6.alarm_code == ERR402


def test_pressure_bounded_and_floored():
    r100 = generate_reading("EQP-003", "Etching", anomaly=True, step=100, rng=_rng())
    # 저압 평형점(0.85) 부근, 물리 하한 아래로는 내려가지 않음
    assert r100.pressure >= Decimal(str(MIN_PRESSURE))
    assert Decimal("0.75") <= r100.pressure <= Decimal("0.95")
