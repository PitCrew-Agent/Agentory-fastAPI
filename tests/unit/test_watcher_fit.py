"""watcher.fit 순수 로직 단위 테스트"""

from datetime import UTC, datetime

import numpy as np

from agentory.core.config import get_settings
from agentory.modules.watcher.fit import (
    _clamped_equipment_limit,
    _effective_since,
    _limit_from_parts,
    _limit_from_windows,
)
from anomaly.models.pca_mspc import PcaMspc
from anomaly.windowing import sliding_windows

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)


def test_effective_since_takes_later_bound():
    # 최근성 경계와 정비 시점 중 늦은 쪽 (정비 이전 열화 정상 제외)
    assert _effective_since(T1, T2) == T2
    assert _effective_since(T2, T1) == T2


def test_effective_since_handles_none():
    assert _effective_since(None, T2) == T2
    assert _effective_since(T1, None) == T1
    assert _effective_since(None, None) is None


def _normal(rng, n):
    base = rng.normal(0, 1, size=(n, 1))
    return np.concatenate([base, base + rng.normal(0, 0.1, size=(n, 1))], axis=1)


def _fitted_model(rng):
    return PcaMspc(0.9, 0.999, cross_correlation=True).fit(
        sliding_windows(_normal(rng, 8000), 12, 6)
    )


def test_limit_excludes_ewma_warmup_spike():
    # 시퀀스 앞부분 급등(EWMA 초기값 오염)이 한계를 부풀리지 않아야 함
    rng = np.random.default_rng(3)
    model = _fitted_model(rng)
    clean = sliding_windows(_normal(rng, 6000), 12, 6)
    spiked = clean.copy()
    spiked[:20] += 8.0  # 초반 20 윈도우만 큰 이탈 (워밍업 구간)
    settings = get_settings()
    baseline = _limit_from_windows(clean, model, settings)
    with_spike = _limit_from_windows(spiked, model, settings)
    assert with_spike < baseline * 1.2  # 워밍업 제외로 한계 거의 불변


def test_limit_from_parts_avoids_boundary_transient():
    # 파트별 EWMA 결합이 이어붙인 시퀀스 대비 경계 전이로 부풀지 않아야 함
    rng = np.random.default_rng(5)
    model = _fitted_model(rng)
    settings = get_settings()
    parts = [sliding_windows(_normal(rng, 3000), 12, 6) for _ in range(4)]
    pooled = _limit_from_parts(parts, model, settings)
    concatenated = _limit_from_windows(np.concatenate(parts), model, settings)
    assert pooled <= concatenated * 1.05


def test_clamped_equipment_limit_bounds_ratio():
    settings = get_settings()
    process = 1.0
    low = settings.anomaly_calibration_min_ratio
    high = settings.anomaly_calibration_max_ratio
    # 비율 밖 값은 잘리고, 안쪽 값은 그대로
    assert _clamped_equipment_limit(10.0, process, settings) == high
    assert _clamped_equipment_limit(0.001, process, settings) == low
    assert _clamped_equipment_limit(1.1, process, settings) == 1.1
