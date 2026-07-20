"""Kernel PCA 재구성 잔차 이상 감지 (EXP-009 재구성 잔차 계열)

PCA의 비선형 확장, RBF 커널 부분공간에 사영 후 원공간 복원 오차를 이상도로 사용
"PCA가 선형이라 낡았는가"를 재구성 지표를 유지한 채 정면 비교하는 상대

커널 행렬이 표본 제곱이라 학습 표본을 subsample로 제한 (전체 적합 불가)
inverse_transform은 학습된 pre-image 근사라 PCA 대비 재구성이 부정확할 수 있음
"""

import numpy as np
from sklearn.decomposition import KernelPCA
from sklearn.preprocessing import StandardScaler

from anomaly.features import window_features

MAX_FIT_SAMPLES = 3000  # 커널 행렬 메모리 상한, 초과 시 무작위 축소


class KernelPcaResidual:
    """anomaly.models.base.AnomalyDetector 구현 (비선형 재구성 잔차)"""

    def __init__(
        self,
        n_components: int = 12,
        gamma: float | None = None,
        quantile: float = 0.999,
        cross_correlation: bool = True,
        seed: int = 0,
    ) -> None:
        self.n_components = n_components
        self.gamma = gamma
        self.quantile = quantile
        self.cross_correlation = cross_correlation
        self.seed = seed
        self._scaler = StandardScaler()
        self._kpca = KernelPCA(
            n_components=n_components,
            kernel="rbf",
            gamma=gamma,
            fit_inverse_transform=True,
            random_state=seed,
        )
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "KernelPcaResidual":
        features = self._scaler.fit_transform(window_features(windows, self.cross_correlation))
        rng = np.random.default_rng(self.seed)
        if features.shape[0] > MAX_FIT_SAMPLES:
            idx = rng.choice(features.shape[0], MAX_FIT_SAMPLES, replace=False)
            features = features[idx]
        self._kpca.fit(features)
        self._limit = max(float(np.quantile(self._residual(features), self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        if self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        return self._residual(features) / self._limit

    def _residual(self, features: np.ndarray) -> np.ndarray:
        reconstructed = self._kpca.inverse_transform(self._kpca.transform(features))
        return np.sum((features - reconstructed) ** 2, axis=1)
