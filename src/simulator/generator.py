"""센서 값 생성·판정 순수 로직 (BE_SIM01_GEN01)

SPC 기반 동적 밴드 모델 (참고서 §2·§3)
- 각 변수는 중심선(mu0 + 드리프트) 주변 정규분포로 생성, 밴드는 중심선±3sigma로 함께 이동
- 급성 이상(값이 동적 밴드 밖) → ERR-401·ERR-301·ERR-201·WRN-501
- 드리프트/PM(밴드가 하드리밋 회랑 소진) → WRN-701~704
- 변동성 증가(최근 창 1차 차분 표준편차가 정상의 2배 초과, 추세 무관) → WRN-801
- 복합 냉각 고장(온도 급성↑ + 압력 급성↓ 동반) → 대표 알람 ERR-402 (매뉴얼 §5.1)
- 변수마다 독립 판정, 후보 중 우선순위 최상위 1개를 그 변수 알람으로 선정
- 대표 알람(alarm_code)은 복합 냉각 고장(ERR-402) 우선, 없으면 변수별 최고 심각도
- 연속 2 tick 지속 판정은 상위 실행부(main)가 수행

DB·시간 의존 없이 순수 함수라 단위 테스트가 결정론적으로 검증 가능
"""

import math
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from statistics import pstdev

from simulator.profiles import SensorProfile, VariableSpec, get_profile
from simulator.scenarios import FAULT_OVERSHOOT, VARIANCE_MULT, ScenarioSpec

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
VARIANCE = "WRN-801"  # 변동성 증가
COMPOSITE_COOLING = "ERR-402"  # 복합 냉각 고장 (온도 급성↑ + 압력 급성↓), 매뉴얼 §5.1

VARIANCE_MIN_SAMPLES = 5  # WRN-801 판정 최소 표본 수
# iid 노이즈의 1차 차분 표준편차는 sqrt(2)*sigma, 노이즈 2배면 2*sqrt(2)*sigma 초과
_VARIANCE_DIFF_FACTOR = 2 * math.sqrt(2)

# 알람 우선순위, 작을수록 우선 (참고서 §7)
# 변수 내 후보 선택과 변수 간 대표값(worst-of) 선정에 공통 사용, ERR-402 복합 냉각 고장이 최우선
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
    "WRN-801": 6,
}

_TWO_PLACES = Decimal("0.01")


@dataclass
class SensorReading:
    equipment_id: str
    temperature: Decimal
    pressure: Decimal
    rf_power: Decimal
    gas_flow: Decimal
    alarm_code: (
        str | None
    )  # 대표 알람(복합 ERR-402 우선, 없으면 변수별 최고 심각도), 지속 판정 전 원시값
    alarm_codes: dict[str, str | None] = field(default_factory=dict)  # 변수별 후보 알람
    composite_code: str | None = None  # 복합 대표 알람(ERR-402), 변수별 코드와 별개 오버레이


def _dec(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _drift_rate(var: str, scenario: ScenarioSpec, spec: VariableSpec, gain: float = 1.0) -> float:
    # 시나리오별 변수 드리프트 레이트 결정, gain은 느린 PM 드리프트(WRN-70x)에만 적용
    if var in scenario.drift_vars:
        return spec.drift_rate * gain
    return 0.0


def judge_variables(
    profile: SensorProfile,
    values: dict[str, float],
    bands: dict[str, tuple[float, float]],
    history: list[SensorReading] | None = None,
    *,
    variance_vars: tuple[str, ...] = (),
) -> dict[str, str | None]:
    # 변수마다 독립적으로 후보를 모아 우선순위 최상위 1개를 그 변수 알람으로 채택
    # variance_vars는 잡음 증폭이 의도된 변수라 밴드 이탈을 급성으로 오분류하지 않음
    result: dict[str, str | None] = {var: None for var in VARS}
    have_variance_window = history is not None and len(history) + 1 >= VARIANCE_MIN_SAMPLES

    for var in VARS:
        spec = getattr(profile, var)
        lcl, ucl = bands[var]
        candidates: list[str] = []

        # 급성: 값이 동적 밴드 밖 (변동성 의도 변수는 잡음 이탈이므로 급성 제외)
        if not (lcl <= values[var] <= ucl) and var not in variance_vars:
            candidates.append(ACUTE_CODES[var])
        # 드리프트/PM: 밴드가 하드리밋 회랑을 소진
        if ucl >= spec.usl - spec.band_half or lcl <= spec.lsl + spec.band_half:
            candidates.append(DRIFT_CODES[var])
        # 변동성: 최근 창 1차 차분 표준편차가 정상 대비 2배 초과, 드리프트 추세와 무관
        if have_variance_window:
            recent = [float(getattr(r, var)) for r in history] + [values[var]]
            diffs = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
            if pstdev(diffs) > _VARIANCE_DIFF_FACTOR * spec.sigma:
                candidates.append(VARIANCE)

        if candidates:
            result[var] = min(candidates, key=lambda code: PRIORITY[code])
    return result


def representative(codes: dict[str, str | None]) -> str | None:
    # 변수별 알람 중 최고 심각도 1개를 대표 알람으로 선정 (하위 소비 모듈·SSE 호환용)
    present = [code for code in codes.values() if code is not None]
    if not present:
        return None
    return min(present, key=lambda code: PRIORITY[code])


def detect_cooling_fault(
    values: dict[str, float], bands: dict[str, tuple[float, float]]
) -> str | None:
    # 온도↑·압력↓ 동시 밴드 이탈 시 복합 냉각 고장 → 대표 ERR-402 (매뉴얼 §5.1)
    temp_high = values["temperature"] > bands["temperature"][1]
    pressure_low = values["pressure"] < bands["pressure"][0]
    return COMPOSITE_COOLING if temp_high and pressure_low else None


COMPOSITE_METRIC = "temperature"  # 복합 냉각 고장 대표 채널 (온도 축 서술 기준, 매뉴얼 §5.1)
COMPOSITE_SUPPRESSED = ("temperature", "pressure")  # 복합 발령 시 억제할 단일 변수


def surface_codes(codes: dict[str, str | None], composite: str | None) -> dict[str, str | None]:
    # 복합이면 온도·압력 단일 코드 억제·대표 채널에 ERR-402 부여 (매뉴얼 "단일로 쪼개지 않음")
    if composite is None:
        return codes
    surfaced = dict(codes)
    for metric in COMPOSITE_SUPPRESSED:
        surfaced[metric] = None
    surfaced[COMPOSITE_METRIC] = composite
    return surfaced


def generate_reading(
    equipment_id: str,
    process_type: str | None,
    *,
    scenario: ScenarioSpec,
    tick: int = 0,
    drift_start_tick: int = 0,
    history: list[SensorReading] | None = None,
    gain: float = 1.0,
    rng: random.Random,
) -> SensorReading:
    """단일 설비 센서값 1건 생성 + 해당 tick 변수별 후보 알람 판정

    scenario의 드리프트 대상 변수는 중심선이 tick에 따라 이동, 나머지는 mu0 유지
    variance_vars 변수는 sigma를 증폭해 변동성 증가를 모사, history는 WRN-801 판정용 최근값
    gain은 PM 드리프트 레이트 배수로 데모 가시성 조절 (기본 1.0=실측 프로파일 그대로)
    """
    profile = get_profile(process_type)
    active = tick >= drift_start_tick
    values: dict[str, float] = {}
    bands: dict[str, tuple[float, float]] = {}

    for var in VARS:
        spec = getattr(profile, var)
        drift = _drift_rate(var, scenario, spec, gain) * max(0, tick - drift_start_tick)
        center = spec.mu0 + drift
        if var in scenario.drift_vars:
            # PM 드리프트는 밴드가 하드리밋 회랑 소진 시 포화 → WRN-70x
            center = min(max(center, spec.lsl + spec.band_half), spec.usl - spec.band_half)
        else:
            # 급성 폭주 방지, 하드리밋 바깥 현실적 고장폭으로 포화
            span = spec.usl - spec.lsl
            center = min(
                max(center, spec.lsl - FAULT_OVERSHOOT * span), spec.usl + FAULT_OVERSHOOT * span
            )
        # 변동성 시나리오 대상 변수는 sigma 증폭 (밴드는 nominal 유지)
        sigma = spec.sigma * (VARIANCE_MULT if active and var in scenario.variance_vars else 1.0)
        value = center + rng.gauss(0, sigma)
        # 급성 단일변수 이상: 값을 밴드 밖(USL 바로 바깥)으로 계단 이탈 → ACUTE 알람 (센터 불변)
        if active and var in scenario.acute_vars:
            value += (spec.usl - spec.mu0) + spec.band_half
        # 하향 급성: 값을 LSL 바로 바깥으로 계단 이탈 (온도 급성↑ 동반 시 복합 ERR-402)
        if active and var in scenario.acute_low_vars:
            value -= (spec.mu0 - spec.lsl) + spec.band_half
        values[var] = value
        bands[var] = (center - spec.band_half, center + spec.band_half)

    codes = judge_variables(profile, values, bands, history, variance_vars=scenario.variance_vars)
    composite = detect_cooling_fault(values, bands)
    return SensorReading(
        equipment_id=equipment_id,
        temperature=_dec(values["temperature"]),
        pressure=_dec(values["pressure"]),
        rf_power=_dec(values["rf_power"]),
        gas_flow=_dec(values["gas_flow"]),
        alarm_code=composite or representative(codes),
        alarm_codes=codes,
        composite_code=composite,
    )
