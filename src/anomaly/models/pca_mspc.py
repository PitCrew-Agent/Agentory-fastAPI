"""PCA 기반 MSPC 이상 감지 (v0, EXP-002)

정상 윈도우 피처로 PCA 부분공간을 적합하고 두 통계량으로 이상도 산출
- T² (Hotelling): 부분공간 안에서 정상 중심 대비 과도한 변동
- SPE (Q statistic): 부분공간 밖 잔차, 정상에 없던 변동 패턴 (상관 붕괴)
최종 점수 = max(T²/T²한계, SPE/SPE한계), 1.0 초과가 판정 기준
한계는 학습 데이터 점수의 quantile로 산출 (분포 가정 없는 경험적 한계)
"""

import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from anomaly.features import window_features


class PcaMspc:
    """anomaly.models.base.AnomalyDetector 구현

    n_components는 유지할 분산 비율(0~1), quantile은 학습 점수 기반 한계 분위수
    """

    def __init__(self, n_components: float = 0.9, quantile: float = 0.997) -> None:
        self.n_components = n_components
        self.quantile = quantile
        self._scaler = StandardScaler()
        self._pca = PCA(n_components=n_components, svd_solver="full")
        self._t2_limit: float | None = None
        self._spe_limit: float | None = None

    def fit(self, windows: np.ndarray) -> "PcaMspc":
        """정상 운전 윈도우 (N, W, C)로 부분공간·한계 적합"""
        features = self._scaler.fit_transform(window_features(windows))
        self._pca.fit(features)
        t2, spe = self._statistics(features)
        # 0 한계 방지, 학습 분산이 극단적으로 작아도 나눗셈 안정 유지
        self._t2_limit = max(float(np.quantile(t2, self.quantile)), 1e-12)
        self._spe_limit = max(float(np.quantile(spe, self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 이상도 점수 (N,), 1.0 초과 = 정상 한계 밖"""
        if self._t2_limit is None or self._spe_limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        features = self._scaler.transform(window_features(windows))
        t2, spe = self._statistics(features)
        return np.maximum(t2 / self._t2_limit, spe / self._spe_limit)

    def _statistics(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # T²: 주성분 점수를 고유값으로 정규화한 마할라노비스 제곱
        # SPE: 부분공간 재구성 잔차 제곱합
        projected = self._pca.transform(features)
        t2 = np.sum(projected**2 / self._pca.explained_variance_, axis=1)
        reconstructed = self._pca.inverse_transform(projected)
        spe = np.sum((features - reconstructed) ** 2, axis=1)
        return t2, spe
