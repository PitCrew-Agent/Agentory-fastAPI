"""설비 공정 유형별 센서 SPC 프로파일 (BE_SIM01_GEN01)

각 변수의 중심값(mu0)·표준편차(sigma)·정상 밴드 반폭(band_half=3sigma)·하드리밋(LSL/USL)·
권장 드리프트 레이트를 정의 (시뮬레이터 구현 참고서 §2 기준)
Etching은 참고서 실측 기반 권장값, 그 외 공정은 라인 구분용 데모 파생값
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VariableSpec:
    """센서 변수 1개의 SPC 파라미터"""

    mu0: float  # 중심값
    sigma: float  # 표준편차
    band_half: float  # 정상 밴드 반폭 (3sigma)
    lsl: float  # 하드리밋 하한
    usl: float  # 하드리밋 상한
    drift_rate: float  # 권장 드리프트 (부호 포함, tick당 변화)


@dataclass(frozen=True)
class SensorProfile:
    temperature: VariableSpec  # °C
    pressure: VariableSpec  # mTorr
    rf_power: VariableSpec  # kW
    gas_flow: VariableSpec  # sccm


# Etching 권장 운영 프로파일 (참고서 §2, 실측 기반)
_ETCHING = SensorProfile(
    temperature=VariableSpec(60.00, 0.10, 0.30, 58.50, 61.50, 0.03),
    pressure=VariableSpec(42.00, 0.67, 2.00, 30.00, 55.00, 0.40),
    rf_power=VariableSpec(2.79, 0.027, 0.08, 2.45, 3.05, -0.006),
    gas_flow=VariableSpec(606.00, 3.00, 9.00, 540.00, 660.00, -2.00),
)

# Deposition 데모 파생 프로파일 (참고서는 Etching 기준, 라인 구분용 임시값)
_DEPOSITION = SensorProfile(
    temperature=VariableSpec(45.00, 0.10, 0.30, 43.50, 46.50, 0.03),
    pressure=VariableSpec(30.00, 0.50, 1.50, 22.00, 40.00, 0.30),
    rf_power=VariableSpec(1.80, 0.020, 0.06, 1.55, 2.05, -0.004),
    gas_flow=VariableSpec(400.00, 2.50, 7.50, 350.00, 450.00, -1.50),
)

PROFILES: dict[str, SensorProfile] = {
    "Etching": _ETCHING,
    "Deposition": _DEPOSITION,
}

DEFAULT_PROFILE = _ETCHING


def get_profile(process_type: str | None) -> SensorProfile:
    # 미지정·미등록 공정은 Etching 기준으로 대체
    if process_type is None:
        return DEFAULT_PROFILE
    return PROFILES.get(process_type, DEFAULT_PROFILE)
