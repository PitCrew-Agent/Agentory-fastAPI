"""윈도우 피처 추출 (v0 핸드크래프트)

(N, W, C) 윈도우 배열을 채널별 요약 통계 피처 (N, C*6)로 변환
피처 6종: 평균·표준편차·최소·최대·선형 기울기·1차 차분 표준편차
급성(평균·최소·최대), 드리프트(기울기), 변동성(차분 표준편차) 이상 축을 각각 커버
"""

import numpy as np

FEATURES_PER_CHANNEL = 6


def window_features(windows: np.ndarray) -> np.ndarray:
    """(N, W, C) → (N, C*6) 채널별 요약 통계, 채널 순서 유지

    W < 2면 기울기·차분이 정의되지 않아 ValueError
    """
    if windows.ndim != 3:
        raise ValueError("windows는 (N, W, C) 3차원 배열만 허용")
    n, width, _channels = windows.shape
    if width < 2:
        raise ValueError("윈도우 길이는 2 이상만 허용")
    if n == 0:
        return np.empty((0, windows.shape[2] * FEATURES_PER_CHANNEL))

    mean = windows.mean(axis=1)
    std = windows.std(axis=1)
    minimum = windows.min(axis=1)
    maximum = windows.max(axis=1)
    # 선형 기울기: slope = cov(t, x) / var(t), 시간축 중심화로 벡터화 계산
    t = np.arange(width, dtype=float) - (width - 1) / 2
    slope = np.einsum("w,nwc->nc", t, windows - mean[:, None, :]) / (t @ t)
    diff_std = np.diff(windows, axis=1).std(axis=1)

    # (N, C, 6) → (N, C*6), 채널별 피처 블록 연속 배치
    stacked = np.stack([mean, std, minimum, maximum, slope, diff_std], axis=2)
    return stacked.reshape(n, -1)
