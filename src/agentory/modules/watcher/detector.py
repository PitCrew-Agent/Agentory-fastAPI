"""이상 징후 감지 스코어러 (NEW_PROACT01_DETECT01, BE_ANOM01_SERVE01)

규칙 임계가 못 잡는 임계 안쪽 이상을 PCAX + EWMA 경로로 감지 (EXP-006·007 확정)
학습된 공정 유형별 모델을 적재해 최근 윈도우 점수를 산출, 지속 K확인 후 발령 판정
순수 로직만 담아 DB·시간 의존 없이 단위 테스트 가능
"""

from dataclasses import dataclass

import numpy as np

from agentory.modules.telemetry.schemas import alarm_severity
from anomaly.models.pca_mspc import PcaMspc
from anomaly.models.var_residual import VarResidual
from anomaly.scoring import ewma_masked, sustained, transition_mask
from anomaly.windowing import sliding_windows

# 센서 채널 순서, telemetry 모델 컬럼·학습 피처 순서와 일치 유지
VARS: tuple[str, ...] = ("temperature", "pressure", "rf_power", "gas_flow")

# 이상 감지 발령 코드·심각도, 규칙 코드(ERR/WRN-50x~80x)와 구분되는 통계 이상 코드
# 다변량 관계 붕괴는 위험 등급 (매뉴얼 상태 판정), 심각도는 telemetry 단일 소스 사용
ANOMALY_ALARM_CODE = "WRN-901"
ANOMALY_SEVERITY = alarm_severity(ANOMALY_ALARM_CODE)


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
    transition_threshold: float  # 전이 억제 raw 임계 (EXP-008), 0이면 억제 없음
    transition_settle: int  # 전이 후 정착 억제 윈도우 수
    # VAR 보완 경로 (BE_ANOM01_VAR01), 상관 붕괴 전담·None이면 미사용
    var_model: VarResidual | None = None
    var_ewma_limit: float | None = None


@dataclass(frozen=True)
class ScoreResult:
    """최근 윈도우 판정 결과, channel은 발령 시 최대 기여 센서"""

    fired: bool
    channel: str | None
    score: float


class AnomalyScorer:
    """공정 유형별 모델 보관·최근 시계열 판정"""

    def __init__(
        self, models: dict[str, LoadedModel], equipment_limits: dict[str, float] | None = None
    ) -> None:
        self._models = models
        # 설비별 EWMA 한계 override, 미보유 설비는 공정 유형 한계로 폴백 (BE_ANOM01_CALIB01)
        self._equipment_limits = equipment_limits or {}

    @property
    def process_types(self) -> set[str]:
        return set(self._models)

    def has(self, process_type: str) -> bool:
        return process_type in self._models

    def score_latest(
        self, process_type: str, equipment_id: str, series: np.ndarray
    ) -> ScoreResult | None:
        """최근 센서 시계열 (T, C)로 마지막 윈도우 발령 여부 판정

        모델 미보유·윈도우 부족 시 None (스킵), EWMA 경로 단일 판정 (EXP-007 확정 config)
        설비별 캘리브 한계가 있으면 우선, 없으면 공정 유형 한계 사용 (BE_ANOM01_CALIB01)
        """
        loaded = self._models.get(process_type)
        if loaded is None:
            return None
        windows = sliding_windows(series, loaded.window, loaded.stride)
        if windows.shape[0] == 0:
            return None

        raw = loaded.model.score(windows)
        # 급격한 레짐 변화(모드 전이) 구간을 EWMA·발령에서 제외 (EXP-008)
        mask = transition_mask(raw, loaded.transition_threshold, loaded.transition_settle)
        limit = self._equipment_limits.get(equipment_id, loaded.ewma_limit)
        normalized = self._accumulate(raw, loaded, mask, limit)
        fired_series = sustained(normalized > 1.0, loaded.confirm_k) & ~mask
        latest_fired = bool(fired_series[-1])
        latest_score = float(normalized[-1])
        source = loaded.model

        # VAR 보완 경로, 상관 붕괴를 재구성 경로보다 빠르게 포착 (EXP-009 발견 3)
        # 두 경로를 OR로 병합해 설비당 단일 발령 유지, 전이 억제 마스크는 공유
        if loaded.var_model is not None and loaded.var_ewma_limit:
            var_raw = loaded.var_model.score(windows)
            var_normalized = self._accumulate(var_raw, loaded, mask, loaded.var_ewma_limit)
            var_fired = bool((sustained(var_normalized > 1.0, loaded.confirm_k) & ~mask)[-1])
            if var_fired and not latest_fired:
                source = loaded.var_model  # 귀속은 실제 발령한 경로 기준
            latest_fired = latest_fired or var_fired
            latest_score = max(latest_score, float(var_normalized[-1]))

        channel: str | None = None
        if latest_fired:
            channel = VARS[int(source.top_channel(windows[-1:])[0])]
        return ScoreResult(fired=latest_fired, channel=channel, score=latest_score)

    @staticmethod
    def _accumulate(
        raw: np.ndarray, loaded: LoadedModel, mask: np.ndarray, limit: float
    ) -> np.ndarray:
        # 클리핑 + 전이 구간 제외 EWMA 후 한계 정규화, 경로 공통 후처리
        accumulated = ewma_masked(np.minimum(raw, loaded.ewma_clip), loaded.ewma_alpha, mask)
        return accumulated / limit
