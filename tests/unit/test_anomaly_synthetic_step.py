"""anomaly.synthetic_step 레시피 스텝 생성기 단위 테스트"""

import numpy as np
import pytest

from anomaly.synthetic_step import (
    RECIPE_STEPS,
    WAFER_TICKS,
    generate_stepped_rows,
)

STEP_NAMES = [s[0] for s in RECIPE_STEPS]


def _col(rows, key):
    return np.array([r[key] for r in rows])


def test_wafer_cycles_through_all_steps():
    rows = generate_stepped_rows("EQP-T", "normal", WAFER_TICKS, 0, seed=1)
    steps = set(_col(rows, "step"))
    assert steps == set(STEP_NAMES)
    # 스텝 순서가 정의 순서대로 등장
    seq = [r["step"] for r in rows]
    first_idx = [seq.index(s) for s in STEP_NAMES]
    assert first_idx == sorted(first_idx)


def test_step_centers_differ():
    # main etch는 RF 중심이 stabilization보다 뚜렷이 높음 (스텝별 정상 물리 상이)
    rows = generate_stepped_rows("EQP-T", "normal", WAFER_TICKS * 3, 0, seed=2)
    rf = _col(rows, "rf_power")
    steps = _col(rows, "step")
    main_rf = rf[steps == "main_etch"].mean()
    stab_rf = rf[steps == "stabilization"].mean()
    assert main_rf > stab_rf * 1.5


RAMP_SKIP = 15  # main etch 진입 전이 램프 제외 (중심 이동이 상관 측정 오염)


def _steady_main_slice(rows, tick_min):
    # tick_min 이상 첫 연속 main_etch의 정착 구간(램프 제외)을 (gas, pressure)로 반환
    idx = []
    for i, r in enumerate(rows):
        if r["step"] == "main_etch" and r["tick"] >= tick_min:
            idx.append(i)
        elif idx:
            break
    idx = idx[RAMP_SKIP:]
    gas = np.array([rows[i]["gas_flow"] for i in idx])
    pressure = np.array([rows[i]["pressure"] for i in idx])
    return gas, pressure


def test_corr_break_weakens_gas_pressure_coupling_in_main_etch():
    # 상관 붕괴는 main etch 정착 구간에서 가스-압력 결합을 정상 대비 약화
    onset = WAFER_TICKS * 2
    rows = generate_stepped_rows("EQP-T", "corr_break_main", WAFER_TICKS * 6, onset, seed=3)

    def lag1(gas, pressure):
        return float(np.corrcoef(gas[:-1], pressure[1:])[0, 1])

    normal = lag1(*_steady_main_slice(rows, 0))
    active = lag1(*_steady_main_slice(rows, onset))
    assert normal > 0.3  # 정상은 지연 결합 유지
    assert active < normal - 0.2  # 이상은 결합이 뚜렷이 약화


def test_messy_normal_inflates_marginal_variance():
    # 지저분한 정상은 양성 마진 변동으로 표준편차가 커짐, 결합(관계)은 유지
    def main_pressure(messy):
        rows = generate_stepped_rows("EQP-T", "normal", WAFER_TICKS * 8, 0, seed=3, messy=messy)
        rows = [r for r in rows if r["step"] == "main_etch"]
        return _col(rows, "pressure")

    clean = main_pressure(False)
    messy = main_pressure(True)
    assert messy.std() > clean.std()  # messy가 더 지저분


def test_messy_scale_monotonically_increases_variance():
    # 강도 스윕 축, messy_scale이 클수록 마진 변동이 커짐 (견고성 경계 실험 전제)
    def pressure_std(scale):
        rows = generate_stepped_rows(
            "EQP-T", "normal", WAFER_TICKS * 8, 0, seed=3, messy=True, messy_scale=scale
        )
        return _col([r for r in rows if r["step"] == "main_etch"], "pressure").std()

    assert pressure_std(0.5) < pressure_std(2.0) < pressure_std(4.0)


def test_nonlinear_and_weak_break_variants_run():
    # 비선형 결합·약한 붕괴 옵션이 유효 시나리오로 생성
    nl = generate_stepped_rows("EQP-T", "normal", WAFER_TICKS, 0, seed=6, nonlinear=True)
    weak = generate_stepped_rows(
        "EQP-T", "corr_break_weak", WAFER_TICKS * 4, WAFER_TICKS * 2, seed=6
    )
    assert len(nl) == WAFER_TICKS
    assert {r["step"] for r in weak} == set(STEP_NAMES)


def test_deterministic_and_unknown_scenario():
    a = generate_stepped_rows("EQP-T", "normal", 200, 0, seed=5)
    b = generate_stepped_rows("EQP-T", "normal", 200, 0, seed=5)
    assert a == b
    with pytest.raises(ValueError):
        generate_stepped_rows("EQP-T", "no_such", 100, 0, seed=5)
