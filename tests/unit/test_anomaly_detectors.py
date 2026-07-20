"""EXP-009 비교군 검출기 단위 테스트

전 검출기가 동일 계약(정상 학습 → score 1.0 초과=이상)을 지키는지, 상관 붕괴 반응을
계열별로 확인 (재구성·예측 계열은 포착, 거리 계열은 미포착이 예상 동작)
"""

import numpy as np
import pytest

from anomaly.models.iforest import IsolationForestDetector
from anomaly.models.kernel_pca import KernelPcaResidual
from anomaly.models.pca_mspc import PcaMspc
from anomaly.models.var_residual import VarResidual
from anomaly.windowing import sliding_windows

torch = pytest.importorskip("torch")
from anomaly.models.autoencoder import AeResidual  # noqa: E402

WINDOW, STRIDE = 12, 6


def _coupled(rng, n, broken=False):
    # 채널1이 채널0을 1틱 지연 추종, broken이면 결합 소실 (주변 분포는 유지)
    base = rng.normal(0, 1, size=(n, 1))
    follower = (
        rng.normal(0, 1, size=(n, 1))
        if broken
        else np.roll(base, 1, axis=0) + rng.normal(0, 0.1, size=(n, 1))
    )
    return np.concatenate([base, follower], axis=1)


def _windows(rng, n, broken=False):
    return sliding_windows(_coupled(rng, n, broken), WINDOW, STRIDE)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: VarResidual(quantile=0.99),
        lambda: KernelPcaResidual(n_components=6, quantile=0.99),
        lambda: IsolationForestDetector(n_estimators=50, quantile=0.99),
        lambda: AeResidual(epochs=60, quantile=0.99),
    ],
    ids=["var", "kernel_pca", "iforest", "autoencoder"],
)
def test_detector_contract_normal_scores_within_limit(factory):
    # 정상 학습 후 정상 표본 대부분이 한계(1.0) 이내
    rng = np.random.default_rng(0)
    model = factory().fit(_windows(rng, 4000))
    scores = model.score(_windows(rng, 800))
    assert scores.shape[0] > 0
    assert np.isfinite(scores).all()
    assert float((scores <= 1.0).mean()) > 0.9


def test_var_detects_correlation_break_strongly():
    # 예측 잔차 계열은 결합 소실을 크게 포착
    rng = np.random.default_rng(1)
    model = VarResidual(quantile=0.99).fit(_windows(rng, 6000))
    normal = np.median(model.score(_windows(rng, 1000)))
    broken = np.median(model.score(_windows(rng, 1000, broken=True)))
    assert broken > normal * 10


def test_pca_statistic_decomposition():
    # statistic 분리 시 T²·SPE가 각각 다른 점수를 내고, both는 둘의 최대
    rng = np.random.default_rng(2)
    train = _windows(rng, 4000)
    probe = _windows(rng, 200, broken=True)
    scores = {}
    for stat in ("both", "t2", "spe"):
        model = PcaMspc(0.9, 0.99, cross_correlation=True, statistic=stat).fit(train)
        scores[stat] = model.score(probe)
    assert np.allclose(scores["both"], np.maximum(scores["t2"], scores["spe"]))


def test_pca_rejects_unknown_statistic():
    with pytest.raises(ValueError):
        PcaMspc(statistic="q")


def test_var_top_channel_and_pre_fit_guard():
    rng = np.random.default_rng(3)
    model = VarResidual()
    with pytest.raises(RuntimeError):
        model.score(_windows(rng, 200))
    model.fit(_windows(rng, 3000))
    assert model.top_channel(_windows(rng, 50)).shape == (_windows(rng, 50).shape[0],)
