"""TS2Vec 대조 자기지도 encoder + kNN 거리 이상 감지 (v1, EXP-005)

TS2Vec (Yue et al., AAAI 2022)의 압축 구현
- encoder: 입력 투영 → dilated conv 잔차 블록 스택 → 시점별 representation
- 학습: 같은 윈도우의 겹치는 두 crop을 timestamp 마스킹과 함께 인코딩,
  겹침 구간에 계층적 대조 손실 (instance 대조 + temporal 대조, max pool 피라미드)
- 윈도우 임베딩: 시점별 representation의 max pool

원 논문 대비 단순화 (윈도우 24 tick 규모에 맞춤)
- crop을 긴 시계열이 아니라 입력 윈도우 내부에서 샘플링
- 학습·판정 모두 CPU 고정 (MPS 비결정성 회피, 재현 우선)

판정: 정상 윈도우 임베딩 은행에 대한 kNN 평균 거리 / 학습 분위수 한계 (D3)
대조군으로 단일 centroid cosine 방식도 제공 (baseline="cosine", D3 검증용)
"""

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.neighbors import NearestNeighbors
from torch import nn

_MASK_P = 0.5  # 학습 시 timestamp 마스킹 비율 (TS2Vec binomial mask)


class _ConvBlock(nn.Module):
    """dilated conv 잔차 블록, 시간 길이 보존 (same padding)"""

    def __init__(self, channels: int, dilation: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(channels, channels, 3, padding=dilation, dilation=dilation)
        self.conv2 = nn.Conv1d(channels, channels, 3, padding=dilation, dilation=dilation)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        x = self.conv1(F.gelu(x))
        x = self.conv2(F.gelu(x))
        return x + residual


class _TsEncoder(nn.Module):
    """(B, T, C) → (B, T, D) 시점별 representation"""

    def __init__(self, in_dims: int, hidden: int, out_dims: int, depth: int) -> None:
        super().__init__()
        self.input_fc = nn.Linear(in_dims, hidden)
        self.blocks = nn.Sequential(*[_ConvBlock(hidden, 2**i) for i in range(depth)])
        self.repr_conv = nn.Conv1d(hidden, out_dims, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        x = self.input_fc(x)  # (B, T, H)
        if mask is not None:
            x = x * mask[:, :, None]  # 마스킹 시점의 잠재 입력 0 처리
        x = self.blocks(x.transpose(1, 2))  # (B, H, T)
        return self.repr_conv(x).transpose(1, 2)  # (B, T, D)


def _instance_loss(z1: torch.Tensor, z2: torch.Tensor) -> torch.Tensor:
    # 같은 timestamp에서 배치 내 다른 인스턴스와 대조, positive는 다른 view의 자기 자신
    batch = z1.size(0)
    if batch == 1:
        return z1.new_zeros(())
    z = torch.cat([z1, z2], dim=0).transpose(0, 1)  # (T, 2B, D)
    sim = torch.matmul(z, z.transpose(1, 2))  # (T, 2B, 2B)
    sim.diagonal(dim1=1, dim2=2).fill_(float("-inf"))
    log_prob = F.log_softmax(sim, dim=-1)
    positives = torch.arange(2 * batch, device=z1.device).roll(batch)
    return -log_prob[:, torch.arange(2 * batch), positives].mean()


def _temporal_loss(z1: torch.Tensor, z2: torch.Tensor) -> torch.Tensor:
    # 같은 인스턴스 안에서 다른 timestamp와 대조, positive는 다른 view의 같은 시점
    length = z1.size(1)
    if length == 1:
        return z1.new_zeros(())
    z = torch.cat([z1, z2], dim=1)  # (B, 2T, D)
    sim = torch.matmul(z, z.transpose(1, 2))  # (B, 2T, 2T)
    sim.diagonal(dim1=1, dim2=2).fill_(float("-inf"))
    log_prob = F.log_softmax(sim, dim=-1)
    positives = torch.arange(2 * length, device=z1.device).roll(length)
    return -log_prob[:, torch.arange(2 * length), positives].mean()


def _hierarchical_loss(z1: torch.Tensor, z2: torch.Tensor, alpha: float = 0.5) -> torch.Tensor:
    # max pool 피라미드로 여러 시간 스케일에서 대조 (TS2Vec 계층 손실)
    loss = z1.new_zeros(())
    depth = 0
    while z1.size(1) >= 1:
        loss = loss + alpha * _instance_loss(z1, z2)
        if z1.size(1) > 1:
            loss = loss + (1 - alpha) * _temporal_loss(z1, z2)
        depth += 1
        if z1.size(1) == 1:
            break
        z1 = F.max_pool1d(z1.transpose(1, 2), 2).transpose(1, 2)
        z2 = F.max_pool1d(z2.transpose(1, 2), 2).transpose(1, 2)
    return loss / depth


class Ts2VecKnn:
    """anomaly.models.base.AnomalyDetector 구현 (encoder + 거리 판정 분리, D8)

    baseline="knn": 정상 임베딩 은행 kNN 평균 거리 (선택안)
    baseline="cosine": 단일 centroid cosine 거리 (D3 대조군)
    """

    def __init__(
        self,
        repr_dims: int = 64,
        hidden_dims: int = 64,
        depth: int = 6,
        iters: int = 400,
        batch_size: int = 32,
        lr: float = 1e-3,
        k_neighbors: int = 5,
        quantile: float = 0.999,
        baseline: str = "knn",
        seed: int = 0,
    ) -> None:
        if baseline not in ("knn", "cosine"):
            raise ValueError(f"미지원 baseline: {baseline}")
        self.repr_dims = repr_dims
        self.hidden_dims = hidden_dims
        self.depth = depth
        self.iters = iters
        self.batch_size = batch_size
        self.lr = lr
        self.k_neighbors = k_neighbors
        self.quantile = quantile
        self.baseline = baseline
        self.seed = seed
        self._encoder: _TsEncoder | None = None
        self._mu: np.ndarray | None = None
        self._sd: np.ndarray | None = None
        self._knn: NearestNeighbors | None = None
        self._centroid: torch.Tensor | None = None
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "Ts2VecKnn":
        """정상 윈도우 (N, W, C)로 encoder 학습 + 임베딩 은행·한계 구축"""
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        self._mu = windows.mean(axis=(0, 1))
        self._sd = windows.std(axis=(0, 1)) + 1e-12
        data = torch.from_numpy((windows - self._mu) / self._sd).float()

        encoder = _TsEncoder(windows.shape[2], self.hidden_dims, self.repr_dims, self.depth)
        optimizer = torch.optim.AdamW(encoder.parameters(), lr=self.lr)
        n, width = data.size(0), data.size(1)
        encoder.train()
        for _ in range(self.iters):
            idx = torch.from_numpy(rng.choice(n, min(self.batch_size, n), replace=False))
            batch = data[idx]
            # 겹치는 두 crop: [0, right) / [left, W), 겹침 [left, right)
            left = int(rng.integers(1, width // 2))
            right = int(rng.integers(width // 2 + 1, width))
            mask1 = (torch.rand(batch.size(0), right) > _MASK_P).float()
            mask2 = (torch.rand(batch.size(0), width - left) > _MASK_P).float()
            z1 = encoder(batch[:, :right], mask1)[:, left:]  # 겹침 구간 표현
            z2 = encoder(batch[:, left:], mask2)[:, : right - left]
            loss = _hierarchical_loss(z1, z2)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        encoder.eval()
        self._encoder = encoder

        bank = self._embed(data)
        if self.baseline == "knn":
            self._knn = NearestNeighbors(n_neighbors=self.k_neighbors + 1).fit(bank)
            # 학습 은행 자기 자신(거리 0) 제외한 k 이웃 평균 거리로 한계 산출
            dist, _ = self._knn.kneighbors(bank)
            train_scores = dist[:, 1:].mean(axis=1)
        else:
            centroid = torch.from_numpy(bank).mean(dim=0)
            self._centroid = centroid / centroid.norm()
            train_scores = self._cosine_scores(bank)
        self._limit = max(float(np.quantile(train_scores, self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 이상도 점수 (N,), 1.0 초과 = 정상 한계 밖"""
        if self._encoder is None or self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        data = torch.from_numpy((windows - self._mu) / self._sd).float()
        emb = self._embed(data)
        if self.baseline == "knn":
            dist, _ = self._knn.kneighbors(emb, n_neighbors=self.k_neighbors)
            return dist.mean(axis=1) / self._limit
        return self._cosine_scores(emb) / self._limit

    def _embed(self, data: torch.Tensor) -> np.ndarray:
        # 시점별 representation의 max pool = 윈도우 임베딩 (TS2Vec instance 표현)
        with torch.no_grad():
            reprs = []
            for start in range(0, data.size(0), 512):
                z = self._encoder(data[start : start + 512])
                reprs.append(z.max(dim=1).values)
            return torch.cat(reprs).numpy()

    def _cosine_scores(self, embeddings: np.ndarray) -> np.ndarray:
        emb = torch.from_numpy(embeddings)
        emb = emb / emb.norm(dim=1, keepdim=True).clamp_min(1e-12)
        return (1.0 - emb @ self._centroid).numpy()
