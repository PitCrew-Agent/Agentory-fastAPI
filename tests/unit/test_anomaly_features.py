"""anomaly.features 단위 테스트"""

import numpy as np
import pytest

from anomaly.features import FEATURES_PER_CHANNEL, window_features


def test_window_features_shape():
    windows = np.random.default_rng(0).normal(size=(5, 10, 3))
    features = window_features(windows)
    assert features.shape == (5, 3 * FEATURES_PER_CHANNEL)


def test_window_features_known_values_on_ramp():
    # 채널 1개, 0..4 램프 → 평균 2, 최소 0, 최대 4, 기울기 1, 차분 표준편차 0
    windows = np.arange(5, dtype=float).reshape(1, 5, 1)
    mean, std, minimum, maximum, slope, diff_std = window_features(windows)[0]
    assert mean == 2.0
    assert minimum == 0.0 and maximum == 4.0
    assert slope == pytest.approx(1.0)
    assert diff_std == pytest.approx(0.0)
    assert std == pytest.approx(np.sqrt(2.0))


def test_window_features_empty_input():
    features = window_features(np.empty((0, 10, 4)))
    assert features.shape == (0, 4 * FEATURES_PER_CHANNEL)


def test_window_features_cross_correlation_block():
    rng = np.random.default_rng(1)
    base = rng.normal(size=(8, 30, 1))
    # 채널2 = 채널1 복제 (상관 1), 채널3 = 독립
    windows = np.concatenate([base, base, rng.normal(size=(8, 30, 1))], axis=2)
    features = window_features(windows, cross_correlation=True)
    assert features.shape == (8, 3 * FEATURES_PER_CHANNEL + 3)
    corr_12 = features[:, 3 * FEATURES_PER_CHANNEL]  # 쌍 순서 (0,1) (0,2) (1,2)
    assert np.allclose(corr_12, 1.0, atol=1e-6)
    corr_13 = features[:, 3 * FEATURES_PER_CHANNEL + 1]
    assert np.all(np.abs(corr_13) < 0.9)


def test_window_features_rejects_invalid_shapes():
    with pytest.raises(ValueError):
        window_features(np.zeros((10, 4)))  # 2차원
    with pytest.raises(ValueError):
        window_features(np.zeros((3, 1, 4)))  # 윈도우 길이 1
