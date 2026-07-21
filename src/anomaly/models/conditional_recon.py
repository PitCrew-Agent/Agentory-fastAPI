"""조건부 복원 이상 감지 (EXP-012 마스킹 계열)

한 채널을 통째로 가리고 나머지 채널의 윈도우로 그 채널을 복원, 복원 오차가 이상 스코어
정상이면 채널 간 연쇄가 유지돼 복원이 잘 되고, 연쇄가 깨지면(상관 붕괴) 복원 오차 급등
변수별 복원 오차가 그대로 기여도 (어느 채널·연쇄가 깨졌는지)

마스킹 목적함수가 조건부 관계 p(x_c | x_다른채널) 학습을 강제
- linear 모드: 채널별 릿지 회귀 (결정론·경량), 선형 연쇄에 충분
- mlp 모드: 채널별 소형 신경망, 비선형 연쇄 포착 여지
"""

import numpy as np


class ConditionalReconstruction:
    """anomaly.models.base.AnomalyDetector 구현 (마스킹 조건부 복원)

    각 채널 c에 대해 (나머지 채널 윈도우 → 채널 c 윈도우) 복원기를 적합
    스코어는 전 채널 복원 오차 합을 학습 분위수로 정규화 (1.0 초과가 이상)
    """

    def __init__(
        self, mode: str = "linear", quantile: float = 0.999, ridge: float = 1.0, seed: int = 0
    ) -> None:
        if mode not in ("linear", "mlp"):
            raise ValueError(f"미지원 mode: {mode}")
        self.mode = mode
        self.quantile = quantile
        self.ridge = ridge
        self.seed = seed
        self._channels = 0
        self._mean = None
        self._std = None
        self._models: list = []  # 채널별 복원기 (linear: (W_mat, bias), mlp: nn.Module)
        self._limit: float | None = None

    def fit(self, windows: np.ndarray) -> "ConditionalReconstruction":
        """정상 윈도우 (N, W, C)로 채널별 조건부 복원기 적합"""
        n, width, channels = windows.shape
        self._channels = channels
        self._mean = windows.mean(axis=(0, 1))
        self._std = windows.std(axis=(0, 1)) + 1e-9
        flat = (windows - self._mean) / self._std  # 채널별 표준화
        self._models = [self._fit_channel(flat, c, width) for c in range(channels)]
        train_scores = self._channel_errors(flat).sum(axis=1)
        self._limit = max(float(np.quantile(train_scores, self.quantile)), 1e-12)
        return self

    def score(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 이상도 점수 (N,), 1.0 초과 = 정상 한계 밖"""
        if self._limit is None:
            raise RuntimeError("fit 이전 score 호출 불가")
        flat = (windows - self._mean) / self._std
        return self._channel_errors(flat).sum(axis=1) / self._limit

    def top_channel(self, windows: np.ndarray) -> np.ndarray:
        """윈도우별 최대 복원 오차 채널 (N,), 이상 설명·metric 부여용"""
        if self._limit is None:
            raise RuntimeError("fit 이전 top_channel 호출 불가")
        flat = (windows - self._mean) / self._std
        return self._channel_errors(flat).argmax(axis=1)

    def _others(self, flat: np.ndarray, c: int) -> np.ndarray:
        # 채널 c를 제외한 나머지 채널 윈도우를 (N, W*(C-1))로 평탄화
        keep = [k for k in range(self._channels) if k != c]
        return flat[:, :, keep].reshape(flat.shape[0], -1)

    def _fit_channel(self, flat: np.ndarray, c: int, width: int):
        x = self._others(flat, c)
        y = flat[:, :, c]  # (N, W)
        if self.mode == "linear":
            # 릿지 최소자승, (X'X + eps I)^-1 X'Y, bias 포함
            xb = np.hstack([x, np.ones((x.shape[0], 1))])
            gram = xb.T @ xb + self.ridge * np.eye(xb.shape[1])
            coef = np.linalg.solve(gram, xb.T @ y)
            return coef
        return self._fit_mlp(x, y)

    def _fit_mlp(self, x: np.ndarray, y: np.ndarray):
        import torch
        from torch import nn

        torch.manual_seed(self.seed)
        model = nn.Sequential(
            nn.Linear(x.shape[1], 64),
            nn.GELU(),
            nn.Linear(64, 64),
            nn.GELU(),
            nn.Linear(64, y.shape[1]),
        )
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
        xt, yt = torch.from_numpy(x).float(), torch.from_numpy(y).float()
        rng = np.random.default_rng(self.seed)
        model.train()
        for _ in range(300):
            idx = torch.from_numpy(rng.choice(xt.size(0), min(256, xt.size(0)), replace=False))
            loss = nn.functional.mse_loss(model(xt[idx]), yt[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        return model

    def _channel_errors(self, flat: np.ndarray) -> np.ndarray:
        # (N, C) 채널별 복원 제곱오차 (윈도우 평균)
        n = flat.shape[0]
        errors = np.empty((n, self._channels))
        for c in range(self._channels):
            x = self._others(flat, c)
            y = flat[:, :, c]
            if self.mode == "linear":
                xb = np.hstack([x, np.ones((n, 1))])
                pred = xb @ self._models[c]
            else:
                import torch

                with torch.no_grad():
                    pred = self._models[c](torch.from_numpy(x).float()).numpy()
            errors[:, c] = ((y - pred) ** 2).mean(axis=1)
        return errors
