"""시뮬레이터 시나리오 정의 (BE_SIM01_GEN01)

각 시나리오는 대상 설비에 어떤 이상을 주입할지 규정
변수별 독립 이상(급성·드리프트·변동성)을 조합으로 표현, 한 설비가 변수마다 다른 상태를 동시에 가짐
복합 코드(ERR-402·ERR-901)는 변수 간 코드 전이를 유발해 은퇴, 이상은 항상 단일변수 코드로 발생
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSpec:
    drift_vars: tuple[str, ...] = ()  # 권장 드리프트(profile.drift_rate) 적용 변수 → WRN-70x
    variance_vars: tuple[str, ...] = ()  # 변동성(sigma) 증폭 변수 → WRN-801
    # 값을 밴드 밖으로 계단 이탈시킬 변수 → ERR-401/301/201·WRN-501
    acute_vars: tuple[str, ...] = ()


SCENARIOS: dict[str, ScenarioSpec] = {
    "normal": ScenarioSpec(),
    # 단일변수 드리프트/PM (참고서 §5) → WRN-701~704
    "temperature_drift_pm": ScenarioSpec(drift_vars=("temperature",)),
    "pressure_drift_pm": ScenarioSpec(drift_vars=("pressure",)),
    "rf_power_drift_pm": ScenarioSpec(drift_vars=("rf_power",)),
    "gas_flow_drift_pm": ScenarioSpec(drift_vars=("gas_flow",)),
    "mixed_pm_demo": ScenarioSpec(drift_vars=("pressure", "rf_power")),
    "variance_increase": ScenarioSpec(variance_vars=("pressure",)),
    # 단일변수 급성 밴드 이탈 (참고서 §4)
    "temperature_acute": ScenarioSpec(acute_vars=("temperature",)),  # ERR-401
    "pressure_acute": ScenarioSpec(acute_vars=("pressure",)),  # ERR-301
    "rf_power_acute": ScenarioSpec(acute_vars=("rf_power",)),  # ERR-201
    "gas_flow_acute": ScenarioSpec(acute_vars=("gas_flow",)),  # WRN-501
    # 한 설비 내 변수별 동시 독립 이상 (급성 + 드리프트)
    # 급성은 즉시·드리프트는 서서히라 발생 시각 자연 분리
    "temp_acute_pressure_drift": ScenarioSpec(
        acute_vars=("temperature",), drift_vars=("pressure",)
    ),  # 온도 ERR-401(위험) + 압력 WRN-702(주의)
    "rf_acute_gas_drift": ScenarioSpec(
        acute_vars=("rf_power",), drift_vars=("gas_flow",)
    ),  # RF ERR-201(위험) + 가스 WRN-704(주의)
    "gas_acute_temp_drift": ScenarioSpec(
        acute_vars=("gas_flow",), drift_vars=("temperature",)
    ),  # 가스 WRN-501(주의) + 온도 WRN-701(주의)
}

NORMAL = SCENARIOS["normal"]

# 데모용 다중 설비 시나리오 배치 (설비 → 시나리오 키), 미지정 설비는 normal
# 4개 변수를 급성·드리프트로 고루 노출하고, 여러 설비가 변수별 동시 독립 이상을 갖도록 배치
PRESETS: dict[str, dict[str, str]] = {
    "floor_demo": {
        "EQP-A05": "temp_acute_pressure_drift",  # 온도 급성(위험) + 압력 드리프트(주의) 동시
        "EQP-A03": "rf_acute_gas_drift",  # RF 급성(위험) + 가스 드리프트(주의) 동시
        "EQP-B04": "gas_acute_temp_drift",  # 가스 급성(주의) + 온도 드리프트(주의) 동시
        "EQP-C03": "pressure_acute",  # 압력 급성(위험) 단일
    },
}

# 급성·드리프트 폭주 시 값이 하드리밋 밖으로 나가는 최대 폭 (하드리밋 span 대비 배수)
# 실제 물리 범위를 벗어난 발산(예: 온도 540) 방지, 현실적 고장 범위로 포화
FAULT_OVERSHOOT = 0.5

# 급성 단일변수 이상: 값을 USL 바로 바깥으로 계단 이탈 (센터·밴드는 nominal 유지)
# 오프셋 = (USL - mu0) + band_half, 변수별 프로파일에서 파생

# 변동성 증가(WRN-801): 대상 변수 sigma 증폭 배수
# 임계(2*sqrt2*sigma) 대비 여유를 크게 둬 최신 tick 기준에서도 알람이 안정적으로 확정되게 함
VARIANCE_MULT = 3.5
