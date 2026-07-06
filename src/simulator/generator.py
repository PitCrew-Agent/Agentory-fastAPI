"""센서 값 생성·판정 순수 로직 (BE_SIM01_GEN01)

SPC 기반 동적 밴드 모델 (참고서 §2·§3)
- 각 변수는 중심선(mu0 + 드리프트) 주변 정규분포로 생성, 밴드는 중심선±3sigma로 함께 이동
- 급성 이상(값이 동적 밴드 밖) → ERR-401·ERR-301·ERR-201·WRN-501
- 냉각 급성 이상(온도 급상승 + 압력 하강) → ERR-402
- 드리프트/PM(밴드가 하드리밋 회랑 소진) → WRN-701~704
- 우선순위(참고서 §7)로 대표 알람 1개 선정, 연속 2 tick 지속 판정은 상위 실행부(main)가 수행

DB·시간 의존 없이 순수 함수라 단위 테스트가 결정론적으로 검증 가능
WRN-801(변동성)·ERR-901(다변량)은 참고서 §5·§6 확장 코드로 후속 구현 대상
"""

import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from simulator.profiles import SensorProfile, VariableSpec, get_profile
from simulator.scenarios import ERR402_PRESSURE_RATE, ERR402_TEMP_RATE, ScenarioSpec

VARS = ("temperature", "pressure", "rf_power", "gas_flow")

# 변수별 급성 밴드 이탈 알람 (참고서 §4)
ACUTE_CODES = {
    "temperature": "ERR-401",
    "pressure": "ERR-301",
    "rf_power": "ERR-201",
    "gas_flow": "WRN-501",
}
# 변수별 드리프트/PM 알람 (참고서 §5)
DRIFT_CODES = {
    "temperature": "WRN-701",
    "pressure": "WRN-702",
    "rf_power": "WRN-703",
    "gas_flow": "WRN-704",
}
ERR402 = "ERR-402"

# 대표 알람 우선순위, 작을수록 우선 (참고서 §7)
PRIORITY = {
    "ERR-402": 1,
    "ERR-401": 2,
    "ERR-301": 2,
    "ERR-201": 3,
    "WRN-501": 4,
    "WRN-701": 5,
    "WRN-702": 5,
    "WRN-703": 5,
    "WRN-704": 5,
}

_TWO_PLACES = Decimal("0.01")


@dataclass
class SensorReading:
    equipment_id: str
    temperature: Decimal
    pressure: Decimal
    rf_power: Decimal
    gas_flow: Decimal
    alarm_code: str | None  # 해당 tick 후보 알람, 연속 지속 판정 전 원시값


def _dec(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _drift_rate(var: str, scenario: ScenarioSpec, spec: VariableSpec) -> float:
    # 시나리오별 변수 드리프트 레이트 결정
    if scenario.err402 and var == "temperature":
        return ERR402_TEMP_RATE
    if scenario.err402 and var == "pressure":
        return ERR402_PRESSURE_RATE
    if var in scenario.drift_vars:
        return spec.drift_rate
    return 0.0


def _judge(
    profile: SensorProfile,
    values: dict[str, float],
    bands: dict[str, tuple[float, float]],
) -> str | None:
    # 참고서 §7 판정, 성립 알람 중 우선순위 최상위 1개 반환
    candidates: list[str] = []

    # ERR-402: 온도가 하드리밋 상단 도달 + 압력이 초기 밴드 하단 아래로 하강
    p = profile.pressure
    if values["temperature"] >= profile.temperature.usl and values["pressure"] < (
        p.mu0 - p.band_half
    ):
        candidates.append(ERR402)

    for var in VARS:
        spec = getattr(profile, var)
        lcl, ucl = bands[var]
        # 급성: 값이 동적 밴드 밖
        if not (lcl <= values[var] <= ucl):
            candidates.append(ACUTE_CODES[var])
        # 드리프트/PM: 밴드가 하드리밋 회랑을 소진
        if ucl >= spec.usl - spec.band_half or lcl <= spec.lsl + spec.band_half:
            candidates.append(DRIFT_CODES[var])

    if not candidates:
        return None
    return min(candidates, key=lambda code: PRIORITY[code])


def generate_reading(
    equipment_id: str,
    process_type: str | None,
    *,
    scenario: ScenarioSpec,
    tick: int = 0,
    drift_start_tick: int = 0,
    rng: random.Random,
) -> SensorReading:
    """단일 설비 센서값 1건 생성 + 해당 tick 후보 알람 판정

    scenario의 드리프트 대상 변수는 중심선이 tick에 따라 이동, 나머지는 mu0 유지
    """
    profile = get_profile(process_type)
    values: dict[str, float] = {}
    bands: dict[str, tuple[float, float]] = {}

    for var in VARS:
        spec = getattr(profile, var)
        drift = _drift_rate(var, scenario, spec) * max(0, tick - drift_start_tick)
        center = spec.mu0 + drift
        value = center + rng.gauss(0, spec.sigma)
        values[var] = value
        bands[var] = (center - spec.band_half, center + spec.band_half)

    return SensorReading(
        equipment_id=equipment_id,
        temperature=_dec(values["temperature"]),
        pressure=_dec(values["pressure"]),
        rf_power=_dec(values["rf_power"]),
        gas_flow=_dec(values["gas_flow"]),
        alarm_code=_judge(profile, values, bands),
    )
