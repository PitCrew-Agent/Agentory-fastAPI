"""anomaly.models.conditional_recon 단위 테스트"""

import numpy as np
import pytest

from anomaly.models.conditional_recon import ConditionalReconstruction
from anomaly.windowing import sliding_windows

torch = pytest.importorskip("torch")


def _coupled(rng, n, broken=False):
    # 채널1·채널2가 공통 소스(채널0)를 지연 추종, broken이면 채널1만 결합 소실
    base = rng.normal(0, 1, size=(n, 1))
    follow1 = (
        rng.normal(0, 1, size=(n, 1))
        if broken
        else np.roll(base, 1, axis=0) + rng.normal(0, 0.1, size=(n, 1))
    )
    follow2 = np.roll(base, 1, axis=0) + rng.normal(0, 0.1, size=(n, 1))
    return np.concatenate([base, follow1, follow2], axis=1)


def _windows(rng, n, broken=False):
    return sliding_windows(_coupled(rng, n, broken), 12, 6)


@pytest.mark.parametrize("mode", ["linear", "mlp"])
def test_normal_within_limit_and_break_flags(mode):
    rng = np.random.default_rng(0)
    model = ConditionalReconstruction(mode=mode, quantile=0.99).fit(_windows(rng, 4000))
    normal = model.score(_windows(rng, 500))
    broken = model.score(_windows(rng, 500, broken=True))
    assert float((normal <= 1.0).mean()) > 0.9
    assert np.median(broken) > np.median(normal) * 2


def test_attribution_points_to_broken_channel():
    # 채널1 결합만 끊으면 최대 복원 오차가 채널1(추종자)에 집중
    rng = np.random.default_rng(1)
    model = ConditionalReconstruction(mode="linear", quantile=0.99).fit(_windows(rng, 4000))
    broken = _windows(rng, 200, broken=True)
    top = model.top_channel(broken)
    assert float((top == 1).mean()) > 0.6


def test_pre_fit_guard_and_invalid_mode():
    with pytest.raises(RuntimeError):
        ConditionalReconstruction().score(np.zeros((1, 12, 2)))
    with pytest.raises(ValueError):
        ConditionalReconstruction(mode="rf")
