"""이상 감지 모델 공통 인터페이스

encoder·스코어러 교체 seam, v0(PCA MSPC)와 v1(TS2Vec + kNN)이 동일 시그니처로 A/B 가능하게 유지
정상 윈도우만으로 적합(fit)하고 윈도우별 이상도 점수(score)를 산출하는 one-class 계약
"""

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AnomalyDetector(Protocol):
    """정상 운전 윈도우로 적합 후 윈도우별 이상도 점수 산출"""

    def fit(self, windows: np.ndarray) -> "AnomalyDetector":
        """정상 운전 윈도우 (N, window, C)로 적합, self 반환"""
        ...

    def score(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 이상도 점수 (N,), 클수록 이상"""
        ...
