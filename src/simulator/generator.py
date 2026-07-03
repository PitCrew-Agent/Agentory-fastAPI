"""센서 값 생성 순수 로직 (BE_SIM01_GEN01)

DB·시간 의존 없이 SensorReading을 생성하므로 단위 테스트가 결정론적으로 검증 가능
정상 모드는 baseline 주변 난수, 이상 모드는 실제 장비처럼 1차 지연 응답(지수 점근)으로
온도 상승·압력 저하를 모사하고 온도가 기준 초과 시 ERR-402를 주입

이상 모델 근거: 냉각 고장 시 발열과 줄어든 냉각이 새 평형점에서 만나므로 온도는
무한 상승이 아니라 고온 평형점(ANOMALY_TEMP_PLATEAU)에 점근하며 그 부근에서 요동
"""

import math
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from simulator.profiles import get_profile

# 이상 시나리오(err402_temp_rise) 평형점·시상수
ANOMALY_TEMP_PLATEAU = 65.0  # 냉각 이상 시 도달하는 고온 평형점 °C
ANOMALY_PRESSURE_PLATEAU = 0.85  # 냉각 이상 시 도달하는 저압 평형점
ANOMALY_TIME_CONSTANT = 1.6  # 평형 접근 시상수(step), 작을수록 빨리 정착
ERR402_TEMP_THRESHOLD = 50.0  # ERR-402 발생 온도 기준
ERR402 = "ERR-402"

# 센서 노이즈 표준편차
NORMAL_TEMP_NOISE = 0.8
NORMAL_PRESSURE_NOISE = 0.03
ANOMALY_TEMP_NOISE = 0.4  # 평형 부근 미세 요동
ANOMALY_PRESSURE_NOISE = 0.02
RF_POWER_NOISE = 0.05
GAS_FLOW_NOISE = 1.5

MIN_PRESSURE = 0.50  # 압력 물리 하한

_TWO_PLACES = Decimal("0.01")


@dataclass
class SensorReading:
    equipment_id: str
    temperature: Decimal
    pressure: Decimal
    rf_power: Decimal
    gas_flow: Decimal
    alarm_code: str | None


def _dec(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _approach(baseline: float, plateau: float, step: int) -> float:
    """baseline에서 plateau로 지수 점근한 값 (1차 지연 응답)

    step 0이면 baseline, step이 커질수록 plateau에 수렴하며 상·하한을 넘지 않음
    """
    progress = 1.0 - math.exp(-step / ANOMALY_TIME_CONSTANT)
    return baseline + (plateau - baseline) * progress


def generate_reading(
    equipment_id: str,
    process_type: str | None,
    *,
    anomaly: bool = False,
    step: int = 0,
    rng: random.Random,
) -> SensorReading:
    """단일 설비 센서값 1건 생성

    anomaly=True면 온도·압력이 평형점으로 점근하며 온도 기준 초과 시 ERR-402 부여
    RF 파워·가스 유량은 냉각 계통 이상과 무관하므로 정상 범위 난수 유지
    """
    profile = get_profile(process_type)

    if anomaly:
        temperature = _approach(profile.temperature, ANOMALY_TEMP_PLATEAU, step)
        temperature += rng.gauss(0, ANOMALY_TEMP_NOISE)
        pressure = _approach(profile.pressure, ANOMALY_PRESSURE_PLATEAU, step)
        pressure += rng.gauss(0, ANOMALY_PRESSURE_NOISE)
        alarm_code = ERR402 if temperature >= ERR402_TEMP_THRESHOLD else None
    else:
        temperature = rng.gauss(profile.temperature, NORMAL_TEMP_NOISE)
        pressure = rng.gauss(profile.pressure, NORMAL_PRESSURE_NOISE)
        alarm_code = None

    rf_power = rng.gauss(profile.rf_power, RF_POWER_NOISE)
    gas_flow = rng.gauss(profile.gas_flow, GAS_FLOW_NOISE)

    return SensorReading(
        equipment_id=equipment_id,
        temperature=_dec(temperature),
        pressure=_dec(max(MIN_PRESSURE, pressure)),
        rf_power=_dec(rf_power),
        gas_flow=_dec(gas_flow),
        alarm_code=alarm_code,
    )
