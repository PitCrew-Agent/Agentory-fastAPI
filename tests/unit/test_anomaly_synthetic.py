"""anomaly.synthetic 단위 테스트, 결합 동역학·임계 안쪽 성질·결정론 검증"""

import numpy as np
import pytest

from anomaly.synthetic import DRIFT_CAP, generate_rows

N_TICKS = 2000
ONSET = 500


def _column(rows: list[dict], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


def _lag1_corr(x: np.ndarray, y: np.ndarray) -> float:
    # corr(x_{t-1}, y_t), Gas→Pressure 1 tick 지연 결합 검증용
    return float(np.corrcoef(x[:-1], y[1:])[0, 1])


def test_normal_has_gas_pressure_coupling():
    rows = generate_rows("SYN-T01", "normal", N_TICKS, ONSET, seed=1)
    corr = _lag1_corr(_column(rows, "gas_flow"), _column(rows, "pressure"))
    assert corr > 0.5


def test_corr_break_decouples_after_onset_within_band():
    rows = generate_rows("SYN-T02", "corr_break", N_TICKS, ONSET, seed=1)
    gas, pressure = _column(rows, "gas_flow"), _column(rows, "pressure")
    before = _lag1_corr(gas[:ONSET], pressure[:ONSET])
    after = _lag1_corr(gas[ONSET:], pressure[ONSET:])
    assert before > 0.5
    assert abs(after) < 0.2
    # 임계 안쪽 성질: 규칙 판정(알람) 비율이 정상 수준(우연 이탈)에 머무름
    alarm_rate = np.mean([r["alarm_code"] is not None for r in rows[ONSET:]])
    assert alarm_rate < 0.05


def test_inband_drift_capped_and_rule_invisible():
    rows = generate_rows("SYN-T03", "inband_drift", 4000, ONSET, seed=2)
    temp = _column(rows, "temperature")
    # 드리프트 후반 평균이 상한(1.5sigma = 0.15) 부근까지 상승하되 초과하지 않음
    late_mean = temp[-500:].mean() - 60.0
    assert 0.10 < late_mean < DRIFT_CAP * 0.10 + 0.05
    # 밴드가 중심을 추종하므로 드리프트 알람(WRN-701)은 원리적으로 발생 불가
    assert all(r["alarm_temperature"] != "WRN-701" for r in rows)
    # 급성(ERR-401)은 정상에도 있는 3sigma 확률 이탈만 허용, 드리프트 기인 증가 없음
    acute_rate = np.mean([r["alarm_temperature"] == "ERR-401" for r in rows])
    assert acute_rate < 0.01


def test_ignition_ramps_to_nominal_without_events():
    rows = generate_rows("SYN-T04", "ignition", 1000, 0, seed=3)
    rf = _column(rows, "rf_power")
    # 시작부는 중심 0.2배 부근, 램프 종료 후 정상 중심 복귀
    assert rf[0] < 1.0
    assert abs(rf[500:].mean() - 2.79) < 0.1


def test_generate_rows_deterministic_and_schema():
    rows_a = generate_rows("SYN-T05", "osc_propagation", 300, 100, seed=4)
    rows_b = generate_rows("SYN-T05", "osc_propagation", 300, 100, seed=4)
    assert rows_a == rows_b
    expected_keys = {
        "equipment_id",
        "tick",
        "scenario",
        "alarm_code",
        "temperature",
        "pressure",
        "rf_power",
        "gas_flow",
        "alarm_temperature",
        "alarm_pressure",
        "alarm_rf_power",
        "alarm_gas_flow",
    }
    assert expected_keys == set(rows_a[0].keys())


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        generate_rows("SYN-T06", "no_such", 10, 0, seed=5)
