"""anomaly.scoring 단위 테스트"""

import numpy as np
import pytest

from anomaly.scoring import ewma


def test_ewma_known_values():
    scores = np.array([1.0, 1.0, 3.0])
    out = ewma(scores, alpha=0.5)
    # 초기값 1.0 → 1.0 → 0.5*1+0.5*1=1.0 → 0.5*3+0.5*1=2.0
    assert out.tolist() == [1.0, 1.0, 2.0]


def test_ewma_reduces_noise_variance():
    rng = np.random.default_rng(0)
    noise = rng.normal(size=5000)
    smoothed = ewma(noise, alpha=0.2)
    # 정상 잡음 분산 축소 비율은 alpha/(2-alpha) 근사
    assert smoothed.var() < noise.var() * 0.2


def test_ewma_sustained_signal_passes_through():
    # 지속 신호는 시정수 이후 원 수준 유지 (누적 감지의 근거)
    scores = np.concatenate([np.zeros(50), np.full(50, 0.8)])
    smoothed = ewma(scores, alpha=0.3)
    assert smoothed[-1] == pytest.approx(0.8, abs=1e-4)


def test_ewma_rejects_invalid_alpha_and_handles_empty():
    with pytest.raises(ValueError):
        ewma(np.array([1.0]), alpha=0.0)
    with pytest.raises(ValueError):
        ewma(np.array([1.0]), alpha=1.5)
    assert ewma(np.array([]), alpha=0.5).size == 0
