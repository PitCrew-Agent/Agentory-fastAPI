"""이상 징후 감지 스코어러 (NEW_PROACT01_DETECT01, BE_ANOM01_SERVE01)

규칙 임계가 못 잡는 임계 안쪽 이상을 PCAX + EWMA 경로로 감지 (EXP-006·007 확정)
학습된 공정 유형별 모델을 적재해 최근 윈도우 점수를 산출, 지속 K확인 후 발령 판정
순수 로직만 담아 DB·시간 의존 없이 단위 테스트 가능
"""

from dataclasses import dataclass

import numpy as np

from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma, sustained
from anomaly.windowing import sliding_windows

# 센서 채널 순서, telemetry 모델 컬럼·학습 피처 순서와 일치 유지
VARS: tuple[str, ...] = ("temperature", "pressure", "rf_power", "gas_flow")

# 이상 감지 발령 코드·심각도, 규칙 코드(ERR/WRN-50x~80x)와 구분되는 통계 이상 코드
ANOMALY_ALARM_CODE = "WRN-901"
ANOMALY_SEVERITY = "주의"


@dataclass(frozen=True)
class LoadedModel:
    """적재된 공정 유형별 모델과 서빙 파라미터"""

    model: PcaMspc
    window: int
    stride: int
    confirm_k: int
    ewma_alpha: float
    ewma_clip: float
    ewma_limit: float  # EWMA 재캘리브레이션 한계 (fit 시 산출)


@dataclass(frozen=True)
class ScoreResult:
    """최근 윈도우 판정 결과, channel은 발령 시 최대 기여 센서"""

    fired: bool
    channel: str | None
    score: float


class AnomalyScorer:
    """공정 유형별 모델 보관·최근 시계열 판정"""

    def __init__(self, models: dict[str, LoadedModel]) -> None:
        self._models = models

    @property
    def process_types(self) -> set[str]:
        return set(self._models)

    def has(self, process_type: str) -> bool:
        return process_type in self._models

    def score_latest(self, process_type: str, series: np.ndarray) -> ScoreResult | None:
        """최근 센서 시계열 (T, C)로 마지막 윈도우 발령 여부 판정

        모델 미보유·윈도우 부족 시 None (스킵), EWMA 경로 단일 판정 (EXP-007 확정 config)
        """
        loaded = self._models.get(process_type)
        if loaded is None:
            return None
        windows = sliding_windows(series, loaded.window, loaded.stride)
        if windows.shape[0] == 0:
            return None

        raw = loaded.model.score(windows)
        accumulated = ewma(np.minimum(raw, loaded.ewma_clip), loaded.ewma_alpha)
        normalized = accumulated / loaded.ewma_limit
        fired_series = sustained(normalized > 1.0, loaded.confirm_k)

        latest_fired = bool(fired_series[-1])
        channel: str | None = None
        if latest_fired:
            idx = int(loaded.model.top_channel(windows[-1:])[0])
            channel = VARS[idx]
        return ScoreResult(fired=latest_fired, channel=channel, score=float(normalized[-1]))
