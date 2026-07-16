"""anomaly.scoring.sustained 단위 테스트"""

import numpy as np

from anomaly.scoring import sustained


def test_sustained_requires_k_consecutive():
    over = np.array([True, False, True, True, True, False, True, True])
    # k=2 → run 길이 2 이상인 위치만 True
    result = sustained(over, k=2)
    assert result.tolist() == [False, False, False, True, True, False, False, True]


def test_sustained_k3():
    over = np.array([True, True, True, True, False, True, True])
    result = sustained(over, k=3)
    assert result.tolist() == [False, False, True, True, False, False, False]


def test_sustained_k1_is_identity():
    over = np.array([True, False, True])
    assert sustained(over, k=1).tolist() == over.tolist()


def test_sustained_suppresses_isolated_spikes():
    # 고립된 단발 초과 5개는 전부 억제
    over = np.array([False, True, False, True, False, True, False])
    assert not sustained(over, k=2).any()


def test_sustained_empty():
    assert sustained(np.array([], dtype=bool), k=2).size == 0
