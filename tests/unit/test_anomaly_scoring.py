"""anomaly.scoring 단위 테스트"""

import numpy as np
import pytest

from anomaly.scoring import ewma, ewma_masked, transition_mask


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


def test_transition_mask_flags_spike_and_settle():
    raw = np.array([0.5, 2000.0, 0.4, 0.3, 0.3, 0.2])
    # 임계 1000 초과(idx1)와 이후 settle 2개(idx2,3) 억제
    mask = transition_mask(raw, threshold=1000.0, settle=2)
    assert mask.tolist() == [False, True, True, True, False, False]


def test_transition_mask_disabled_when_threshold_nonpositive():
    raw = np.array([5000.0, 0.1])
    assert not transition_mask(raw, threshold=0.0, settle=3).any()


def test_ewma_masked_skips_suppressed_accumulation():
    # 억제 구간의 거대 값이 EWMA에 유입되지 않음
    scores = np.array([1.0, 1.0, 100.0, 100.0, 1.0])
    mask = np.array([False, False, True, True, False])
    out = ewma_masked(scores, alpha=0.5, mask=mask)
    # idx2,3은 직전값 1.0 유지, idx4는 1.0에서 재개 (100 미유입)
    assert out[2] == pytest.approx(1.0)
    assert out[3] == pytest.approx(1.0)
    assert out[4] == pytest.approx(1.0)


def test_ewma_masked_inits_from_first_unmasked():
    # 전이로 시작하는 시퀀스, 첫 값(억제)이 초기 acc를 오염시키지 않음
    scores = np.array([100.0, 100.0, 0.2, 0.2])
    mask = np.array([True, True, False, False])
    out = ewma_masked(scores, alpha=0.5, mask=mask)
    # 첫 비억제(idx2, 값 0.2)에서 초기화 → 억제 구간도 0.2 유지, 100 미반영
    assert out.max() < 1.0


def test_ewma_masked_all_masked_returns_zero():
    scores = np.array([5.0, 6.0])
    assert not ewma_masked(scores, alpha=0.5, mask=np.array([True, True])).any()
