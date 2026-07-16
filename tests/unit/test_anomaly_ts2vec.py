"""anomaly.models.ts2vec 단위 테스트, 소형 설정 합성 데이터 검증

torch는 experiment 그룹 전용 의존성이라 미설치 환경(CI 기본)에서는 스킵
"""

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from anomaly.models.ts2vec import Ts2VecKnn  # noqa: E402

TINY = dict(repr_dims=8, hidden_dims=8, depth=2, iters=30, batch_size=16, seed=0)


def _normal_windows(rng: np.random.Generator, n: int) -> np.ndarray:
    # 채널 2개 결합 (x2 = x1 지연 없이 추종 + 소잡음)
    base = rng.normal(0.0, 1.0, size=(n, 16, 1))
    return np.concatenate([base, base + rng.normal(0.0, 0.1, size=(n, 16, 1))], axis=2)


def test_knn_scores_flag_mean_shift():
    rng = np.random.default_rng(3)
    model = Ts2VecKnn(**TINY, quantile=0.99).fit(_normal_windows(rng, 300))
    normal_scores = model.score(_normal_windows(rng, 100))
    shifted_scores = model.score(_normal_windows(rng, 100) + 4.0)
    assert np.mean(normal_scores <= 1.0) > 0.85
    assert np.median(shifted_scores) > np.median(normal_scores) * 2


def test_cosine_baseline_variant_runs():
    rng = np.random.default_rng(3)
    model = Ts2VecKnn(**TINY, quantile=0.99, baseline="cosine").fit(_normal_windows(rng, 200))
    scores = model.score(_normal_windows(rng, 50))
    assert scores.shape == (50,)
    assert np.isfinite(scores).all()


def test_fit_is_deterministic_with_seed():
    rng_a, rng_b = np.random.default_rng(5), np.random.default_rng(5)
    eval_windows = np.random.default_rng(9).normal(size=(20, 16, 2))
    scores_a = Ts2VecKnn(**TINY).fit(_normal_windows(rng_a, 200)).score(eval_windows)
    scores_b = Ts2VecKnn(**TINY).fit(_normal_windows(rng_b, 200)).score(eval_windows)
    assert np.allclose(scores_a, scores_b)


def test_score_before_fit_raises():
    with pytest.raises(RuntimeError):
        Ts2VecKnn(**TINY).score(np.zeros((1, 16, 2)))


def test_invalid_baseline_rejected():
    with pytest.raises(ValueError):
        Ts2VecKnn(baseline="euclid")
