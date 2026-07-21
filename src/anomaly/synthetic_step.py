"""레시피 스텝 구조 합성 생성기 (EXP-011, 배치성 Etch 공정 모사)

웨이퍼 한 장 = 하나의 배치, 그 안에 stabilization → main etch → over-etch 스텝 구간
각 스텝은 채널별 중심(setpoint)이 달라 정상 물리가 구분됨, 스텝 경계는 짧은 전이로 연결
결합 동역학(가스→압력, RF→온도)은 스텝 내부에서 유지되고 중심만 스텝마다 이동

기존 synthetic.py(단일 정상 모드)와 분리, 스텝 인지 실험 전용
스텝 라벨을 함께 산출해 스텝 무시 vs 스텝 인지 비교에 사용
"""

import math
import random
from collections import deque

from simulator.generator import VARS
from simulator.profiles import get_profile

# 스텝 정의, (이름, tick 길이, 채널별 중심 배수), 배수는 Etching mu0 기준
RECIPE_STEPS: list[tuple[str, int, dict[str, float]]] = [
    ("stabilization", 40, {"temperature": 0.7, "pressure": 0.6, "rf_power": 0.2, "gas_flow": 1.0}),
    ("main_etch", 200, {"temperature": 1.0, "pressure": 1.0, "rf_power": 1.0, "gas_flow": 1.0}),
    ("over_etch", 80, {"temperature": 0.95, "pressure": 1.1, "rf_power": 0.7, "gas_flow": 0.85}),
]
TRANSITION_TICKS = 12  # 스텝 경계 선형 전이 길이
WAFER_TICKS = sum(dur for _, dur, _ in RECIPE_STEPS)  # 웨이퍼 1장 = 320 tick

SCENARIOS = ("normal", "corr_break_main", "corr_break_weak", "step_delay")
# 시나리오 → (이벤트 kind, 대상 변수), normal은 미등록
EVENT_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "corr_break_main": ("corr_break", ("gas_flow", "pressure")),
    "corr_break_weak": ("corr_break", ("gas_flow", "pressure")),
    "step_delay": ("step_timing", ("rf_power",)),
}
WEAK_COUPLING = 0.4  # corr_break_weak 잔여 결합 비율 (약한 붕괴, 검출 난도 상승)

OSC_PERIOD = 20
HISTORY_LEN = 8
STEP_DELAY_TICKS = 60  # main etch 진입 지연 (배치 타이밍 이상)


def _step_at(tick_in_wafer: int, delay: int = 0) -> tuple[str, dict[str, float]]:
    # 웨이퍼 내 tick 위치의 스텝과 중심 배수, delay는 stabilization 연장(step_delay 이상)
    boundaries = []
    acc = 0
    for name, dur, mult in RECIPE_STEPS:
        d = dur + delay if name == "stabilization" else dur
        boundaries.append((acc, acc + d, name, mult))
        acc += d
    total = acc
    t = tick_in_wafer % total
    for start, end, name, mult in boundaries:
        if start <= t < end:
            # 스텝 진입 직후 TRANSITION_TICKS 동안 이전 스텝 중심에서 선형 전이
            if t - start < TRANSITION_TICKS and start > 0:
                prev_mult = boundaries[[b[2] for b in boundaries].index(name) - 1][3]
                frac = (t - start) / TRANSITION_TICKS
                blended = {v: prev_mult[v] + frac * (mult[v] - prev_mult[v]) for v in VARS}
                return name, blended
            return name, mult
    return boundaries[-1][2], boundaries[-1][3]


def generate_stepped_rows(
    equipment_id: str,
    scenario: str,
    n_ticks: int,
    onset_tick: int,
    seed: int,
    nonlinear: bool = False,
) -> list[dict]:
    """스텝 구조 세그먼트 1개 생성, 스텝 라벨 포함 행 반환

    잠재 편차(sigma 단위)는 synthetic.py와 동일 결합, 중심만 스텝별로 이동
    nonlinear 시 가스→압력 결합을 포화 비선형(tanh)으로, 선형 모델의 한계 검증용
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"미등록 스텝 시나리오: {scenario}")
    rng = random.Random(f"{seed}:{equipment_id}")
    profile = get_profile("Etching")
    specs = {var: getattr(profile, var) for var in VARS}
    history: deque = deque(maxlen=HISTORY_LEN)

    dev = dict.fromkeys(VARS, 0.0)
    rf_lag = deque([0.0, 0.0], maxlen=2)
    gas_prev = 0.0
    rows: list[dict] = []

    for tick in range(n_ticks):
        active = tick >= onset_tick
        delay = STEP_DELAY_TICKS if (scenario == "step_delay" and active) else 0
        step, mult = _step_at(tick, delay)

        gas = 0.8 * dev["gas_flow"] + rng.gauss(0, 0.6)
        rf = 0.6 * dev["rf_power"] + rng.gauss(0, 0.8)
        # 정상 가스→압력 결합, nonlinear면 포화(tanh) 비선형
        gas_effect = 1.4 * math.tanh(gas_prev) if nonlinear else 0.7 * gas_prev
        # 상관 붕괴: main etch 구간에서만 가스→압력 결합 소실(전체) 또는 약화(부분)
        break_main = active and step == "main_etch"
        if scenario == "corr_break_main" and break_main:
            pressure = 0.3 * rf_lag[-1] + rng.gauss(0, 0.86)
        elif scenario == "corr_break_weak" and break_main:
            pressure = WEAK_COUPLING * gas_effect + 0.3 * rf_lag[-1] + rng.gauss(0, 0.72)
        else:
            pressure = gas_effect + 0.3 * rf_lag[-1] + rng.gauss(0, 0.5)
        temperature = 0.7 * dev["temperature"] + 0.4 * rf_lag[0] + rng.gauss(0, 0.35)

        gas_prev = dev["gas_flow"]
        rf_lag.append(rf)
        dev = {"gas_flow": gas, "pressure": pressure, "rf_power": rf, "temperature": temperature}

        values: dict[str, float] = {}
        for var in VARS:
            spec = specs[var]
            values[var] = spec.mu0 * mult[var] + dev[var] * spec.sigma
        # osc_propagation 대신 스텝 전용 시나리오만 사용, 진동은 v2에서 검증됨
        _ = math, OSC_PERIOD  # 예약 상수 참조 유지

        history.append(values)
        row: dict = {
            "equipment_id": equipment_id,
            "tick": tick,
            "step": step,
            "scenario": scenario,
        }
        for var in VARS:
            row[var] = round(values[var], 2)
        rows.append(row)
    return rows
