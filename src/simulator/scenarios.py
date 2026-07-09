"""시뮬레이터 시나리오 정의 (BE_SIM01_GEN01)

각 시나리오는 대상 설비에 어떤 이상을 주입할지 규정
드리프트 대상·급성 냉각 이상·변동성 증가·다변량 관계 붕괴를 조합으로 표현
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSpec:
    drift_vars: tuple[str, ...] = ()  # 권장 드리프트(profile.drift_rate) 적용 변수 → WRN-70x
    err402: bool = False  # 온도 급상승 + 압력 하강 급성 이상 → ERR-402
    variance_vars: tuple[str, ...] = ()  # 변동성(sigma) 증폭 변수 → WRN-801
    multivariate: bool = False  # rf 상승 + 온도 하강 관계 붕괴 → ERR-901


SCENARIOS: dict[str, ScenarioSpec] = {
    "normal": ScenarioSpec(),
    "err402_temp_rise": ScenarioSpec(err402=True),
    "temperature_drift_pm": ScenarioSpec(drift_vars=("temperature",)),
    "pressure_drift_pm": ScenarioSpec(drift_vars=("pressure",)),
    "rf_power_drift_pm": ScenarioSpec(drift_vars=("rf_power",)),
    "gas_flow_drift_pm": ScenarioSpec(drift_vars=("gas_flow",)),
    "mixed_pm_demo": ScenarioSpec(drift_vars=("pressure", "rf_power")),
    "variance_increase": ScenarioSpec(variance_vars=("pressure",)),
    "multivariate_anomaly": ScenarioSpec(multivariate=True),
}

NORMAL = SCENARIOS["normal"]

# 데모용 다중 설비 시나리오 배치 (설비 → 시나리오 키), 미지정 설비는 normal
# 3D 씬에서 정상 다수 + 서로 다른 경고 + 이상 1종이 한눈에 갈리도록 라인별 분산
PRESETS: dict[str, dict[str, str]] = {
    "floor_demo": {
        "EQP-A05": "err402_temp_rise",  # ERR-402 냉각 급성 이상, 온도 한계 돌파 빨강(위험)
        "EQP-A03": "pressure_drift_pm",  # WRN-702 압력 드리프트(주의)
        "EQP-B04": "gas_flow_drift_pm",  # WRN-704 가스유량 드리프트(주의)
        "EQP-C03": "temperature_drift_pm",  # WRN-701 온도 드리프트(주의)
    },
}

# err402 시나리오의 온도 상승·압력 하강 레이트
ERR402_TEMP_RATE = 0.60  # tick당 온도 상승
ERR402_PRESSURE_RATE = -1.30  # tick당 압력 하강

# 변동성 증가(WRN-801): 대상 변수 sigma 증폭 배수
VARIANCE_MULT = 2.5

# 다변량 관계 붕괴(ERR-901): rf는 서서히 상승, 온도는 서서히 하강 (정상이면 함께 상승)
# 밴드(3sigma) 안에 머물도록 작은 레이트 사용
MULTIVAR_RF_RATE = 0.006  # tick당 rf 상승
MULTIVAR_TEMP_RATE = -0.022  # tick당 온도 하강
