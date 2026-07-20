"""VAR 예측 잔차 이상 감지 (EXP-009 예측 잔차 계열)

각 채널을 전 채널의 과거 lag로 예측하고 잔차 크기를 이상도로 사용
x_t = sum_{i=1..p} A_i x_{t-i} + c + e_t, 계수는 정상 데이터 최소자승으로 적합
이상도는 잔차의 마할라노비스 노름 (정상 잔차 공분산 기준)

상관 붕괴("한 채널이 움직였는데 결합 채널이 따라오지 않음")를 구조적으로 직접 포착
채널별 잔차가 그대로 귀속 근거가 되고, 계수·공분산만 저장하면 되어 결정론적·초경량
"""

import numpy as np

from anomaly.models.base import AnomalyDetector  # noqa: F401  (프로토콜 문서화용)


class VarResidual:
    """anomaly.models.base.AnomalyDetector 구현 (예측 잔차 계열)

    lag는 자기회귀 차수, quantile은 학습 잔차 점수 기반 한계 분위수
    윈도우 내부에서 lag 이후 시점들의 잔차를 계산해 대표값(평균)을 점수로 사용
    """

    def __init__(self, lag: int = 2, quantile: float = 0.999, ridge: float = 1e-6) -> None:
        self.lag = lag
        self.quantile = quantile
        self.ridge = ridge  # 최소자승 정칙화, 공선성 방어
        self._coef: np.ndarray | None = None  # (C, lag*C + 1)
        self._precision: np.ndarray | None = None  # 잔차 공분산 역행렬
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "VarResidual":
        """정상 윈도우 (N, W, C)로 VAR 계수·잔차 공분산·한계 적합"""
        design, target = self._design_target(windows)
        # 릿지 최소자승, (X'X + eps I)^-1 X'Y
        gram = design.T @ design + self.ridge * np.eye(design.shape[1])
        self._coef = np.linalg.solve(gram, design.T @ target).T
        residual = target - design @ self._coef.T
        covariance = np.cov(residual, rowvar=False) + self.ridge * np.eye(residual.shape[1])
        self._precision = np.linalg.inv(np.atleast_2d(covariance))
        self._limit = max(float(np.quantile(self._window_scores(windows), self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 이상도 점수 (N,), 1.0 초과 = 정상 한계 밖"""
        if self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        return self._window_scores(windows) / self._limit

    def top_channel(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 최대 잔차 채널 인덱스 (N,), 이상 설명·metric 부여용"""
        if self._coef is None:
            raise RuntimeError("fit 이전 top_channel 호출 불가")
        residual = self._residuals(windows)  # (N, W-lag, C)
        return np.abs(residual).mean(axis=1).argmax(axis=1)

    def _design_target(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # 전 윈도우의 (lag 시점 묶음 → 다음 시점) 쌍을 쌓아 설계행렬 구성
        lagged, target = [], []
        for i in range(self.lag, windows.shape[1]):
            past = windows[:, i - self.lag : i, :]  # (N, lag, C)
            lagged.append(past.reshape(windows.shape[0], -1))
            target.append(windows[:, i, :])
        design = np.concatenate(lagged)
        bias = np.ones((design.shape[0], 1))
        return np.hstack([design, bias]), np.concatenate(target)

    def _residuals(self, windows: np.ndarray) -> np.ndarray:
        # 윈도우별 시점 잔차 (N, W-lag, C)
        n, width, channels = windows.shape
        out = np.empty((n, width - self.lag, channels))
        for idx, i in enumerate(range(self.lag, width)):
            past = windows[:, i - self.lag : i, :].reshape(n, -1)
            design = np.hstack([past, np.ones((n, 1))])
            out[:, idx, :] = windows[:, i, :] - design @ self._coef.T
        return out

    def _window_scores(self, windows: np.ndarray) -> np.ndarray:
        # 잔차 마할라노비스 제곱의 윈도우 평균
        residual = self._residuals(windows)
        mahalanobis = np.einsum("nwc,cd,nwd->nw", residual, self._precision, residual)
        return mahalanobis.mean(axis=1)
