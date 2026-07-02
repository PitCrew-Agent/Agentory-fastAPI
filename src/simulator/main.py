"""센서 데이터 시뮬레이터 (BE_SIM01_GEN01)

실행: uv run simulator
정상 범위 난수 생성 + 시나리오 모드(온도 점진 상승, ERR-402 주입) 지원,
equipment_telemetry에 주기적 INSERT
골든 E2E 테스트(3주차)의 시나리오 주입에 사용
"""

from dataclasses import dataclass


@dataclass
class ScenarioConfig:
    """이상치 시나리오 설정, 골든 질의 셋 seed_scenario 키와 매핑"""

    name: str = "normal"  # normal | err402_temp_rise
    interval_seconds: int = 60
    target_equipment_id: str = "EQP-003"


def run() -> None:
    # TODO(주희정): 설비별 난수 생성 루프 + 시나리오 모드 + DB 적재 구현
    raise NotImplementedError("BE_SIM01_GEN01 미구현")
