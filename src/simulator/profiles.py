"""설비 공정 유형별 센서 정상 범위 baseline (BE_SIM01_GEN01)

process_type 기준으로 온도·압력·RF 파워·가스 유량 기준값 제공
알 수 없는 유형은 DEFAULT_PROFILE 사용
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorProfile:
    temperature: float  # °C
    pressure: float  # 압력
    rf_power: float  # kW
    gas_flow: float  # sccm


PROFILES: dict[str, SensorProfile] = {
    "Etching": SensorProfile(temperature=42.0, pressure=1.20, rf_power=2.00, gas_flow=150.0),
    "Deposition": SensorProfile(temperature=38.0, pressure=1.10, rf_power=1.50, gas_flow=120.0),
}

DEFAULT_PROFILE = SensorProfile(temperature=40.0, pressure=1.15, rf_power=1.80, gas_flow=140.0)


def get_profile(process_type: str | None) -> SensorProfile:
    if process_type is None:
        return DEFAULT_PROFILE
    return PROFILES.get(process_type, DEFAULT_PROFILE)
