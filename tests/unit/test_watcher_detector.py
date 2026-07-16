"""watcher.detector 이상 스코어러 단위 테스트 (순수 로직)"""

import numpy as np

from agentory.modules.watcher.detector import VARS, AnomalyScorer, LoadedModel
from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma
from anomaly.windowing import sliding_windows


def _coupled_normal(rng: np.random.Generator, n_ticks: int) -> np.ndarray:
    # 4채널, 채널1이 채널0을 추종하는 정상 결합 시계열 (T, 4)
    base = rng.normal(0, 1, size=(n_ticks, 1))
    coupled = base + rng.normal(0, 0.1, size=(n_ticks, 1))
    others = rng.normal(0, 1, size=(n_ticks, 2))
    return np.concatenate([base, coupled, others], axis=1)


def _loaded_model(rng: np.random.Generator, window=12, stride=6, k=2) -> LoadedModel:
    train = sliding_windows(_coupled_normal(rng, 4000), window, stride)
    model = PcaMspc(0.9, 0.999, cross_correlation=True).fit(train)
    limit = float(np.quantile(ewma(np.minimum(model.score(train), 3.0), 0.1), 0.999))
    return LoadedModel(model, window, stride, k, 0.1, 3.0, max(limit, 1e-12))


def test_normal_series_does_not_fire():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    result = scorer.score_latest("Etching", _coupled_normal(rng, 300))
    assert result is not None
    assert result.fired is False


def test_channel_deviation_fires_with_attribution():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    series = _coupled_normal(rng, 300)
    series[150:, 0] += 6.0  # 채널0(temperature) 큰 이탈, 후반 지속
    result = scorer.score_latest("Etching", series)
    assert result is not None
    assert result.fired is True
    assert result.channel == VARS[0]


def test_unknown_process_type_returns_none():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    assert scorer.score_latest("Unknown", _coupled_normal(rng, 300)) is None


def test_insufficient_window_returns_none():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    # 윈도우(12)보다 짧은 시계열 → None
    assert scorer.score_latest("Etching", _coupled_normal(rng, 5)) is None
