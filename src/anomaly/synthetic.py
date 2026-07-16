"""임계 안쪽(in-band) 이상 합성 생성기 (평가 세트 v2, EXP-004)

simulator는 변수별 독립 생성이라 센서 간 상관 이상을 표현할 수 없어, 식각 챔버의
물리 결합(Gas → Pressure, RF → Pressure·Temp)을 지연 선형 결합으로 모사하는 평가 전용
생성기를 별도 유지 (simulator 확장 대신 격리, 데모·골든 테스트 무영향)

시나리오 4종 (모두 규칙 레이어의 사각을 겨냥)
- corr_break: Gas→Pressure 결합 소실 (MFC/밸브 이상), 각 채널 주변 분포는 정상 유지
- osc_propagation: RF 진동이 Pressure·Temp로 지연 전파 (RF generator 이상)
- inband_drift: 온도 중심이 +1.5sigma까지 완만 상승 후 유지 (ESC/Leak 계열),
  동적 밴드가 중심을 추종하므로 규칙 레이어에 원리적으로 비가시
- ignition: 세그먼트 시작부 점화 램프업 (정상 라벨, 다중 모드 오탐 측정용)

규칙 판정은 simulator와 동일 의미론 유지, 밴드는 실제 중심을 추종 (SPC 재계산 모사)
"""

import math
import random
from collections import deque
from decimal import Decimal

from simulator.generator import VARS, SensorReading, judge_variables, representative
from simulator.profiles import get_profile

SCENARIOS = ("normal", "corr_break", "osc_propagation", "inband_drift", "ignition")

# 시나리오 → (이벤트 kind, 대상 변수), ignition은 정상 라벨이라 미등록
EVENT_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "corr_break": ("corr_break", ("gas_flow", "pressure")),
    "osc_propagation": ("oscillation", ("rf_power", "pressure", "temperature")),
    "inband_drift": ("drift_inband", ("temperature",)),
}

PROCESS_TYPE = "EtchingCoupled"  # 결합 동역학 전용 공정 유형, 모델도 이 단위로 분리 적합

IGNITION_TICKS = 120  # 점화 램프업 길이, 중심 0.2배 → 1.0배 선형 상승
DRIFT_RATE = 0.004  # inband 드리프트, tick당 중심 이동 (sigma 단위)
DRIFT_CAP = 1.5  # inband 드리프트 상한 (sigma 단위), 밴드 회랑 소진에 크게 미달
OSC_AMP = 1.2  # RF 진동 진폭 (sigma 단위)
OSC_PERIOD = 20  # RF 진동 주기 (tick)
HISTORY_LEN = 8  # WRN-801 이동창 길이, simulator와 동일


def generate_rows(
    equipment_id: str, scenario: str, n_ticks: int, onset_tick: int, seed: int
) -> list[dict]:
    """세그먼트 1개 생성, eval_set._generate_segment와 동일한 행 스키마 반환

    잠재 편차(sigma 단위) 동역학
    - gas: AR(1) phi 0.8
    - pressure: 0.7*gas(t-1) + 0.3*rf(t-1) + 잡음 (결합이 정상의 정의)
    - rf: AR(1) phi 0.6
    - temperature: AR(1) phi 0.7 + 0.4*rf(t-2)
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"미등록 합성 시나리오: {scenario}")
    rng = random.Random(f"{seed}:{equipment_id}")
    profile = get_profile("Etching")  # 중심·표준편차·밴드는 Etching 프로파일 공유
    specs = {var: getattr(profile, var) for var in VARS}
    history: deque = deque(maxlen=HISTORY_LEN)

    dev = dict.fromkeys(VARS, 0.0)  # 채널별 잠재 편차 (sigma 단위)
    rf_lag = deque([0.0, 0.0], maxlen=2)  # temperature 결합용 rf 편차 지연 버퍼
    gas_prev = 0.0
    drift_center = 0.0
    rows: list[dict] = []

    for tick in range(n_ticks):
        active = tick >= onset_tick

        gas = 0.8 * dev["gas_flow"] + rng.gauss(0, 0.6)
        rf = 0.6 * dev["rf_power"] + rng.gauss(0, 0.8)
        if scenario == "osc_propagation" and active:
            rf += OSC_AMP * math.sin(2 * math.pi * (tick - onset_tick) / OSC_PERIOD)
        if scenario == "corr_break" and active:
            # Gas 결합 소실, 주변 분포는 잡음 확대로 유지 (규칙 레이어 비가시 조건)
            pressure = 0.3 * rf_lag[-1] + rng.gauss(0, 0.86)
        else:
            pressure = 0.7 * gas_prev + 0.3 * rf_lag[-1] + rng.gauss(0, 0.5)
        temperature = 0.7 * dev["temperature"] + 0.4 * rf_lag[0] + rng.gauss(0, 0.35)

        gas_prev = dev["gas_flow"]
        rf_lag.append(rf)
        dev = {"gas_flow": gas, "pressure": pressure, "rf_power": rf, "temperature": temperature}

        if scenario == "inband_drift" and active:
            drift_center = min(drift_center + DRIFT_RATE, DRIFT_CAP)
        # 점화 램프업: 전 채널 중심 0.2배 → 1.0배, 이후 정상 (다중 모드 정상 구간)
        mult = 0.2 + 0.8 * min(tick / IGNITION_TICKS, 1.0) if scenario == "ignition" else 1.0

        values: dict[str, float] = {}
        bands: dict[str, tuple[float, float]] = {}
        for var in VARS:
            spec = specs[var]
            center = spec.mu0 * mult
            if var == "temperature":
                center += drift_center * spec.sigma
            values[var] = center + dev[var] * spec.sigma
            # 밴드는 실제 중심 추종 (simulator의 동적 밴드 의미론과 동일)
            bands[var] = (center - spec.band_half, center + spec.band_half)

        codes = judge_variables(profile, values, bands, list(history))
        history.append(
            SensorReading(
                equipment_id=equipment_id,
                temperature=Decimal(str(values["temperature"])),
                pressure=Decimal(str(values["pressure"])),
                rf_power=Decimal(str(values["rf_power"])),
                gas_flow=Decimal(str(values["gas_flow"])),
                alarm_code=None,
            )
        )

        row: dict = {
            "equipment_id": equipment_id,
            "tick": tick,
            "scenario": scenario,
            "alarm_code": representative(codes),
        }
        for var in VARS:
            row[var] = round(values[var], 2)
            row[f"alarm_{var}"] = codes.get(var)
        rows.append(row)
    return rows
