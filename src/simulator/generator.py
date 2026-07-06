"""센서 값 생성·판정 순수 로직 (BE_SIM01_GEN01)

SPC 기반 동적 밴드 모델 (참고서 §2·§3)
- 각 변수는 중심선(mu0 + 드리프트) 주변 정규분포로 생성, 밴드는 중심선±3sigma로 함께 이동
- 급성 이상(값이 동적 밴드 밖) → ERR-401·ERR-301·ERR-201·WRN-501
- 냉각 급성 이상(온도 급상승 + 압력 하강) → ERR-402
- 드리프트/PM(밴드가 하드리밋 회랑 소진) → WRN-701~704
- 변동성 증가(최근 창 1차 차분 표준편차가 정상의 2배 초과, 추세 무관) → WRN-801
- 다변량 관계 붕괴(전부 밴드 안 + rf 상승·온도 하강) → ERR-901
- 우선순위(참고서 §7)로 대표 알람 1개 선정, 연속 2 tick 지속 판정은 상위 실행부(main)가 수행

DB·시간 의존 없이 순수 함수라 단위 테스트가 결정론적으로 검증 가능
"""

import math
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import pstdev

from simulator.profiles import SensorProfile, VariableSpec, get_profile
from simulator.scenarios import (
    ERR402_PRESSURE_RATE,
    ERR402_TEMP_RATE,
    MULTIVAR_RF_RATE,
    MULTIVAR_TEMP_RATE,
    VARIANCE_MULT,
    ScenarioSpec,
)

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
VARIANCE = "WRN-801"  # 변동성 증가
MULTIVAR = "ERR-901"  # 다변량 복합 이상

VARIANCE_MIN_SAMPLES = 5  # WRN-801 판정 최소 표본 수
MULTIVAR_DEV = 2.0  # ERR-901 관계 붕괴 표준화 편차 임계
# iid 노이즈의 1차 차분 표준편차는 sqrt(2)*sigma, 노이즈 2배면 2*sqrt(2)*sigma 초과
_VARIANCE_DIFF_FACTOR = 2 * math.sqrt(2)

# 대표 알람 우선순위, 작을수록 우선 (참고서 §7)
PRIORITY = {
    "ERR-402": 1,
    "ERR-901": 2,
    "ERR-401": 2,
    "ERR-301": 2,
    "ERR-201": 3,
    "WRN-501": 4,
    "WRN-701": 5,
    "WRN-702": 5,
    "WRN-703": 5,
    "WRN-704": 5,
    "WRN-801": 5,
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
    if scenario.multivariate and var == "rf_power":
        return MULTIVAR_RF_RATE
    if scenario.multivariate and var == "temperature":
        return MULTIVAR_TEMP_RATE
    if var in scenario.drift_vars:
        return spec.drift_rate
    return 0.0


def _judge(
    profile: SensorProfile,
    values: dict[str, float],
    bands: dict[str, tuple[float, float]],
    history: list[SensorReading] | None = None,
) -> str | None:
    # 성립한 알람 후보 중 우선순위 최상위 1개를 대표 알람으로 반환
    candidates: list[str] = []
    all_in_band = all(bands[v][0] <= values[v] <= bands[v][1] for v in VARS)

    # ERR-402: 온도가 하드리밋 상단 도달 + 압력이 초기 밴드 하단 아래로 하강
    p = profile.pressure
    if values["temperature"] >= profile.temperature.usl and values["pressure"] < (
        p.mu0 - p.band_half
    ):
        candidates.append(ERR402)

    # ERR-901: 개별 변수는 모두 밴드 안이나 rf 상승·온도 하강으로 정상 관계가 깨짐
    if all_in_band:
        rf_dev = (values["rf_power"] - profile.rf_power.mu0) / profile.rf_power.sigma
        temp_dev = (values["temperature"] - profile.temperature.mu0) / profile.temperature.sigma
        if rf_dev >= MULTIVAR_DEV and temp_dev <= -MULTIVAR_DEV:
            candidates.append(MULTIVAR)

    for var in VARS:
        spec = getattr(profile, var)
        lcl, ucl = bands[var]
        # 급성: 값이 동적 밴드 밖
        if not (lcl <= values[var] <= ucl):
            candidates.append(ACUTE_CODES[var])
        # 드리프트/PM: 밴드가 하드리밋 회랑을 소진
        if ucl >= spec.usl - spec.band_half or lcl <= spec.lsl + spec.band_half:
            candidates.append(DRIFT_CODES[var])

    # WRN-801: 최근 창 변동성(1차 차분 표준편차)이 정상 대비 2배 초과, 드리프트 추세와 무관
    if history is not None and len(history) + 1 >= VARIANCE_MIN_SAMPLES:
        for var in VARS:
            spec = getattr(profile, var)
            recent = [float(getattr(r, var)) for r in history] + [values[var]]
            diffs = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
            if pstdev(diffs) > _VARIANCE_DIFF_FACTOR * spec.sigma:
                candidates.append(VARIANCE)
                break

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
    history: list[SensorReading] | None = None,
    rng: random.Random,
) -> SensorReading:
    """단일 설비 센서값 1건 생성 + 해당 tick 후보 알람 판정

    scenario의 드리프트 대상 변수는 중심선이 tick에 따라 이동, 나머지는 mu0 유지
    variance_vars 변수는 sigma를 증폭해 변동성 증가를 모사, history는 WRN-801 판정용 최근값
    """
    profile = get_profile(process_type)
    active = tick >= drift_start_tick
    values: dict[str, float] = {}
    bands: dict[str, tuple[float, float]] = {}

    for var in VARS:
        spec = getattr(profile, var)
        drift = _drift_rate(var, scenario, spec) * max(0, tick - drift_start_tick)
        center = spec.mu0 + drift
        # 변동성 시나리오 대상 변수는 sigma 증폭 (밴드는 nominal 유지)
        sigma = spec.sigma * (VARIANCE_MULT if active and var in scenario.variance_vars else 1.0)
        value = center + rng.gauss(0, sigma)
        values[var] = value
        bands[var] = (center - spec.band_half, center + spec.band_half)

    return SensorReading(
        equipment_id=equipment_id,
        temperature=_dec(values["temperature"]),
        pressure=_dec(values["pressure"]),
        rf_power=_dec(values["rf_power"]),
        gas_flow=_dec(values["gas_flow"]),
        alarm_code=_judge(profile, values, bands, history),
    )
