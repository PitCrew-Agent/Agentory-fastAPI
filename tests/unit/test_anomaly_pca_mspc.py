"""anomaly.models.pca_mspc 단위 테스트, 합성 데이터 기반 결정론 검증"""

import numpy as np
import pytest

from anomaly.models.pca_mspc import PcaMspc


def _normal_windows(rng: np.random.Generator, n: int) -> np.ndarray:
    # 채널 2개가 강한 상관을 갖는 정상 패턴 (x2 = x1 + 소잡음)
    base = rng.normal(0.0, 1.0, size=(n, 20, 1))
    coupled = base + rng.normal(0.0, 0.1, size=(n, 20, 1))
    return np.concatenate([base, coupled], axis=2)


def test_score_flags_mean_shift():
    rng = np.random.default_rng(7)
    model = PcaMspc(n_components=0.9, quantile=0.99).fit(_normal_windows(rng, 500))
    normal_scores = model.score(_normal_windows(rng, 200))
    shifted = _normal_windows(rng, 200) + 5.0  # 두 채널 동시 평균 이동
    shifted_scores = model.score(shifted)
    # 정상은 대부분 한계 이내, 이동 구간은 전부 한계 밖
    assert np.mean(normal_scores <= 1.0) > 0.9
    assert np.all(shifted_scores > 1.0)


def test_score_flags_correlation_break():
    # 개별 채널 분포는 유지한 채 채널 간 상관만 붕괴 → SPE가 포착해야 함
    rng = np.random.default_rng(7)
    model = PcaMspc(n_components=0.9, quantile=0.99).fit(_normal_windows(rng, 500))
    broken = np.concatenate(
        [rng.normal(0.0, 1.0, size=(200, 20, 1)), rng.normal(0.0, 1.0, size=(200, 20, 1))],
        axis=2,
    )
    broken_scores = model.score(broken)
    normal_scores = model.score(_normal_windows(rng, 200))
    assert np.median(broken_scores) > np.median(normal_scores) * 2


def test_score_before_fit_raises():
    with pytest.raises(RuntimeError):
        PcaMspc().score(np.zeros((1, 20, 2)))


def test_to_state_from_state_roundtrip():
    rng = np.random.default_rng(11)
    train = _normal_windows(rng, 400)
    model = PcaMspc(n_components=0.9, quantile=0.99, cross_correlation=True).fit(train)
    restored = PcaMspc.from_state(model.to_state())
    probe = _normal_windows(rng, 30)
    # 복원 모델 점수가 원본과 수치 일치
    assert np.allclose(model.score(probe), restored.score(probe))
    assert restored.cross_correlation is True


def test_to_state_before_fit_raises():
    with pytest.raises(RuntimeError):
        PcaMspc().to_state()


def test_top_channel_identifies_deviating_channel():
    rng = np.random.default_rng(11)
    model = PcaMspc(n_components=0.9, quantile=0.99, cross_correlation=True).fit(
        _normal_windows(rng, 400)
    )
    # 채널 0만 크게 이탈시킨 윈도우 → top_channel이 0을 지목
    probe = _normal_windows(rng, 20)
    probe[:, :, 0] += 6.0
    assert (model.top_channel(probe) == 0).mean() > 0.8
