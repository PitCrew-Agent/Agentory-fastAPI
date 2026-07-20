"""Isolation Forest 이상 감지 (EXP-009 거리·밀도 계열 참고점)

트리 분할 깊이로 희소성을 측정, 재구성·채널 귀속이 없어 설명 근거를 못 만듦
거리·밀도 계열이 이 문제에서 어디까지 가는지 참고용으로만 비교
"""

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from anomaly.features import window_features


class IsolationForestDetector:
    """anomaly.models.base.AnomalyDetector 구현 (밀도·희소성 계열)"""

    def __init__(
        self,
        n_estimators: int = 200,
        quantile: float = 0.999,
        cross_correlation: bool = True,
        seed: int = 0,
    ) -> None:
        self.quantile = quantile
        self.cross_correlation = cross_correlation
        self._scaler = StandardScaler()
        self._forest = IsolationForest(
            n_estimators=n_estimators, random_state=seed, contamination="auto"
        )
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "IsolationForestDetector":
        features = self._scaler.fit_transform(window_features(windows, self.cross_correlation))
        self._forest.fit(features)
        # score_samples는 클수록 정상이라 부호 반전해 이상도로 사용
        self._limit = max(
            float(np.quantile(-self._forest.score_samples(features), self.quantile)), 1e-12
        )
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        if self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        return -self._forest.score_samples(features) / self._limit
