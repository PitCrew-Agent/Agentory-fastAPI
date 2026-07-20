"""Autoencoder 재구성 잔차 이상 감지 (EXP-009 재구성 잔차 계열)

윈도우 피처를 병목 차원으로 압축·복원하고 복원 오차를 이상도로 사용
PCA와 동일한 재구성 지표를 유지한 비선형 상대, 채널별 오차로 귀속도 가능
학습은 CPU 고정 (재현 우선), seed 고정으로 결정론 확보
"""

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn

from anomaly.features import FEATURES_PER_CHANNEL, window_features


class _Autoencoder(nn.Module):
    def __init__(self, in_dims: int, hidden: int, bottleneck: int) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(in_dims, hidden), nn.GELU(), nn.Linear(hidden, bottleneck)
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, hidden), nn.GELU(), nn.Linear(hidden, in_dims)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


class AeResidual:
    """anomaly.models.base.AnomalyDetector 구현 (비선형 재구성 잔차)"""

    def __init__(
        self,
        hidden: int = 64,
        bottleneck: int = 8,
        epochs: int = 200,
        batch_size: int = 256,
        lr: float = 1e-3,
        quantile: float = 0.999,
        cross_correlation: bool = True,
        seed: int = 0,
    ) -> None:
        self.hidden = hidden
        self.bottleneck = bottleneck
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.quantile = quantile
        self.cross_correlation = cross_correlation
        self.seed = seed
        self._scaler = StandardScaler()
        self._model: _Autoencoder | None = None
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "AeResidual":
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        features = self._scaler.fit_transform(window_features(windows, self.cross_correlation))
        data = torch.from_numpy(features).float()
        model = _Autoencoder(features.shape[1], self.hidden, self.bottleneck)
        optimizer = torch.optim.AdamW(model.parameters(), lr=self.lr)
        model.train()
        for _ in range(self.epochs):
            idx = torch.from_numpy(rng.choice(data.size(0), min(self.batch_size, data.size(0))))
            batch = data[idx]
            loss = nn.functional.mse_loss(model(batch), batch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        model.eval()
        self._model = model
        self._limit = max(float(np.quantile(self._residual(data), self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        if self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        return self._residual(torch.from_numpy(features).float()) / self._limit

    def top_channel(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 최대 복원오차 채널 (N,), 채널별 피처 블록 잔차 합산"""
        if self._model is None:
            raise RuntimeError("fit 이전 top_channel 호출 불가")
        channels = windows.shape[2]
        features = self._scaler.transform(window_features(windows, self.cross_correlation))
        tensor = torch.from_numpy(features).float()
        with torch.no_grad():
            residual_sq = (tensor - self._model(tensor)).numpy() ** 2
        block = channels * FEATURES_PER_CHANNEL
        return (
            residual_sq[:, :block]
            .reshape(-1, channels, FEATURES_PER_CHANNEL)
            .sum(axis=2)
            .argmax(axis=1)
        )

    def _residual(self, tensor: torch.Tensor) -> np.ndarray:
        with torch.no_grad():
            return ((tensor - self._model(tensor)) ** 2).sum(dim=1).numpy()
