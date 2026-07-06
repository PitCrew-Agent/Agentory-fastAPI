"""시뮬레이터 시나리오 정의 (BE_SIM01_GEN01)

시나리오는 어떤 변수에 드리프트를 걸지(drift_vars)와 급성 냉각 이상(err402) 주입 여부를 규정
참고서 §8 권장 시나리오 세트에 대응
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSpec:
    drift_vars: tuple[str, ...] = ()  # 권장 드리프트(profile.drift_rate) 적용 변수 → WRN-70x
    err402: bool = False  # 온도 급상승 + 압력 하강 급성 이상 → ERR-402


SCENARIOS: dict[str, ScenarioSpec] = {
    "normal": ScenarioSpec(),
    "err402_temp_rise": ScenarioSpec(err402=True),
    "temperature_drift_pm": ScenarioSpec(drift_vars=("temperature",)),
    "pressure_drift_pm": ScenarioSpec(drift_vars=("pressure",)),
    "rf_power_drift_pm": ScenarioSpec(drift_vars=("rf_power",)),
    "gas_flow_drift_pm": ScenarioSpec(drift_vars=("gas_flow",)),
    "mixed_pm_demo": ScenarioSpec(drift_vars=("pressure", "rf_power")),
}

NORMAL = SCENARIOS["normal"]

# err402 급성 이상 드리프트 레이트 (빠른 온도 상승·압력 하강, 참고서 §4 복합 증상)
ERR402_TEMP_RATE = 0.60  # tick당 온도 상승
ERR402_PRESSURE_RATE = -1.00  # tick당 압력 하강
