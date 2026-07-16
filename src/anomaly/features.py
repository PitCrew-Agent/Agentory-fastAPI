"""윈도우 피처 추출 (v0 핸드크래프트)

(N, W, C) 윈도우 배열을 채널별 요약 통계 피처 (N, C*6)로 변환
피처 6종: 평균·표준편차·최소·최대·선형 기울기·1차 차분 표준편차
급성(평균·최소·최대), 드리프트(기울기), 변동성(차분 표준편차) 이상 축을 각각 커버

cross_correlation 활성 시 채널 쌍별 윈도우 내 피어슨 상관 C*(C-1)/2개 추가
채널별 요약 통계는 채널 간 관계를 직접 보지 못해 상관 붕괴 이상에 둔감 (EXP-004 발견),
상관 피처가 그 사각을 보완
"""

import numpy as np

FEATURES_PER_CHANNEL = 6

_EPS = 1e-12  # 상수 채널의 0 분산 나눗셈 보호


def window_features(windows: np.ndarray, cross_correlation: bool = False) -> np.ndarray:
    """(N, W, C) → (N, C*6 [+ C*(C-1)/2]) 피처, 채널 순서 유지

    W < 2면 기울기·차분이 정의되지 않아 ValueError
    """
    if windows.ndim != 3:
        raise ValueError("windows는 (N, W, C) 3차원 배열만 허용")
    n, width, channels = windows.shape
    if width < 2:
        raise ValueError("윈도우 길이는 2 이상만 허용")
    n_features = channels * FEATURES_PER_CHANNEL
    if cross_correlation:
        n_features += channels * (channels - 1) // 2
    if n == 0:
        return np.empty((0, n_features))

    mean = windows.mean(axis=1)
    std = windows.std(axis=1)
    minimum = windows.min(axis=1)
    maximum = windows.max(axis=1)
    # 선형 기울기: slope = cov(t, x) / var(t), 시간축 중심화로 벡터화 계산
    t = np.arange(width, dtype=float) - (width - 1) / 2
    centered = windows - mean[:, None, :]
    slope = np.einsum("w,nwc->nc", t, centered) / (t @ t)
    diff_std = np.diff(windows, axis=1).std(axis=1)

    # (N, C, 6) → (N, C*6), 채널별 피처 블록 연속 배치
    stacked = np.stack([mean, std, minimum, maximum, slope, diff_std], axis=2)
    features = stacked.reshape(n, -1)
    if not cross_correlation:
        return features

    # 채널 쌍별 피어슨 상관: corr_ij = sum(xc_i*xc_j) / (W*std_i*std_j)
    inner = np.einsum("nwc,nwd->ncd", centered, centered) / width
    denom = std[:, :, None] * std[:, None, :] + _EPS
    corr = inner / denom
    upper_i, upper_j = np.triu_indices(channels, k=1)
    return np.concatenate([features, corr[:, upper_i, upper_j]], axis=1)
