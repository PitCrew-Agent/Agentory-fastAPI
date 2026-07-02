"""이상 징후 감지 백그라운드 워처 (NEW_PROACT01_DETECT01)

이동평균·기울기 규칙으로 이상 추세 감지 후 알림 큐에 이벤트 적재
감지 규칙 파라미터(윈도우·임계값·쿨다운)는 테스트 하네스로 검증
"""

from dataclasses import dataclass


@dataclass
class DetectionRule:
    window_size: int = 4  # 이동평균 윈도우 (데이터 개수)
    temp_threshold: float = 60.0  # 온도 임계값 (°C)
    cooldown_seconds: int = 300  # 동일 설비 알림 쿨다운 (NEW_PROACT01_ALERT01)


# TODO(주희정): 주기 실행 잡 구현, 설비별 최근 N개 이동평균·기울기 계산 → 이상 이벤트 생성
