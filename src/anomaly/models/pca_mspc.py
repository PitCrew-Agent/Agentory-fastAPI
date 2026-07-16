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

    def __init__(
        self,
        n_components: float = 0.9,
        quantile: float = 0.997,
        cross_correlation: bool = False,
    ) -> None:
        self.n_components = n_components
        self.quantile = quantile
        self.cross_correlation = cross_correlation  # 채널 쌍 상관 피처 (상관 붕괴 감지 보완)
        self._scaler = StandardScaler()
        self._pca = PCA(n_components=n_components, svd_solver="full")
        self._t2_limit: float | None = None
        self._spe_limit: float | None = None

    def fit(self, windows: np.ndarray) -> "PcaMspc":
        """정상 운전 윈도우 (N, W, C)로 부분공간·한계 적합"""
        features = self._scaler.fit_transform(window_features(windows, self.cross_correlation))
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
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        t2, spe = self._statistics(features)
        return np.maximum(t2 / self._t2_limit, spe / self._spe_limit)

    def top_channel(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 SPE 최대 기여 채널 인덱스 (N,), 이상 설명·metric 부여용

        재구성 잔차를 채널별로 합산, 상관 피처 잔차는 관련 두 채널에 분배
        """
        if self._t2_limit is None:
            raise RuntimeError("fit 이전 top_channel 호출 불가")
        channels = windows.shape[2]
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        projected = self._pca.transform(features)
        residual_sq = (features - self._pca.inverse_transform(projected)) ** 2
        # 채널별 요약 통계 블록(채널당 6개) 잔차 합산
        per_channel = residual_sq[:, : channels * 6].reshape(-1, channels, 6).sum(axis=2)
        if self.cross_correlation:
            pairs = [(i, j) for i in range(channels) for j in range(i + 1, channels)]
            corr_residual = residual_sq[:, channels * 6 :]
            for k, (i, j) in enumerate(pairs):
                per_channel[:, i] += corr_residual[:, k]
                per_channel[:, j] += corr_residual[:, k]
        return per_channel.argmax(axis=1)

    def to_state(self) -> dict:
        """적합 파라미터를 JSON 직렬화 가능한 dict로 추출 (RDS 저장용, pickle 회피)"""
        if self._t2_limit is None:
            raise RuntimeError("fit 이전 to_state 호출 불가")
        return {
            "n_components": self.n_components,
            "quantile": self.quantile,
            "cross_correlation": self.cross_correlation,
            "scaler_mean": self._scaler.mean_.tolist(),
            "scaler_scale": self._scaler.scale_.tolist(),
            "scaler_var": self._scaler.var_.tolist(),
            "pca_components": self._pca.components_.tolist(),
            "pca_mean": self._pca.mean_.tolist(),
            "pca_explained_variance": self._pca.explained_variance_.tolist(),
            "t2_limit": self._t2_limit,
            "spe_limit": self._spe_limit,
        }

    @classmethod
    def from_state(cls, state: dict) -> "PcaMspc":
        """to_state 산출물로 적합된 인스턴스 복원 (재학습 없이 서빙 로드)"""
        model = cls(state["n_components"], state["quantile"], state["cross_correlation"])
        scaler = model._scaler
        scaler.mean_ = np.asarray(state["scaler_mean"])
        scaler.scale_ = np.asarray(state["scaler_scale"])
        scaler.var_ = np.asarray(state["scaler_var"])
        scaler.n_features_in_ = scaler.mean_.shape[0]
        pca = model._pca
        pca.components_ = np.asarray(state["pca_components"])
        pca.mean_ = np.asarray(state["pca_mean"])
        pca.explained_variance_ = np.asarray(state["pca_explained_variance"])
        pca.n_components_ = pca.components_.shape[0]
        pca.n_features_in_ = pca.components_.shape[1]
        model._t2_limit = state["t2_limit"]
        model._spe_limit = state["spe_limit"]
        return model

    def _statistics(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # T²: 주성분 점수를 고유값으로 정규화한 마할라노비스 제곱
        # SPE: 부분공간 재구성 잔차 제곱합
        projected = self._pca.transform(features)
        t2 = np.sum(projected**2 / self._pca.explained_variance_, axis=1)
        reconstructed = self._pca.inverse_transform(projected)
        spe = np.sum((features - reconstructed) ** 2, axis=1)
        return t2, spe
