"""watcher.detector 이상 스코어러 단위 테스트 (순수 로직)"""

from dataclasses import replace

import numpy as np

from agentory.modules.watcher.detector import VARS, AnomalyScorer, LoadedModel
from anomaly.models.pca_mspc import PcaMspc
from anomaly.models.var_residual import VarResidual
from anomaly.scoring import ewma
from anomaly.windowing import sliding_windows

EQP = "EQP-T01"


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
    return LoadedModel(
        model,
        window,
        stride,
        k,
        0.1,
        3.0,
        max(limit, 1e-12),
        transition_threshold=1000.0,
        transition_settle=10,
    )


def test_normal_series_does_not_fire():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    result = scorer.score_latest("Etching", EQP, _coupled_normal(rng, 300))
    assert result is not None
    assert result.fired is False


def test_channel_deviation_fires_with_attribution():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    series = _coupled_normal(rng, 300)
    series[150:, 0] += 6.0  # 채널0(temperature) 큰 이탈, 후반 지속
    result = scorer.score_latest("Etching", EQP, series)
    assert result is not None
    assert result.fired is True
    assert result.channel == VARS[0]


def test_ignition_ramp_is_suppressed():
    # 전이(모드 램프)로 시작하는 시계열은 억제되어 미발령 (EXP-008)
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    series = _coupled_normal(rng, 300)
    series[:120] *= 0.2  # 초반 램프업 모사, 전 채널 저값 (raw 급등)
    result = scorer.score_latest("Etching", EQP, series)
    assert result is not None
    assert result.fired is False


def test_equipment_limit_overrides_process_limit():
    # 설비별 한계가 있으면 우선 적용 (BE_ANOM01_CALIB01)
    rng = np.random.default_rng(1)
    loaded = _loaded_model(rng)
    series = _coupled_normal(rng, 300)
    series[150:, 0] += 6.0  # 공유 한계로는 발령되는 이탈

    shared = AnomalyScorer({"Etching": loaded})
    assert shared.score_latest("Etching", EQP, series).fired is True

    # 해당 설비 한계를 크게 잡으면 같은 시계열이 미발령 (개체 정상 폭이 넓은 경우)
    calibrated = AnomalyScorer({"Etching": loaded}, {EQP: loaded.ewma_limit * 1000})
    assert calibrated.score_latest("Etching", EQP, series).fired is False
    # 캘리브 미보유 설비는 공정 유형 한계로 폴백
    assert calibrated.score_latest("Etching", "EQP-OTHER", series).fired is True


def _lagged_normal(rng: np.random.Generator, n_ticks: int, broken_from: int | None = None):
    """지연 결합 정상 시계열 (T, 4), 채널1이 채널0을 1틱 뒤 추종

    실제 챔버 물리(Gas 변화가 시간차를 두고 Pressure에 반영)와 평가셋 v2 결합 구조에 대응
    VAR은 과거 lag만 사용하므로 동시점 상관이 아니라 지연 결합에서 강점을 가짐
    """
    base = rng.normal(0, 1, size=(n_ticks, 1))
    follower = np.roll(base, 1, axis=0) + rng.normal(0, 0.1, size=(n_ticks, 1))
    if broken_from is not None:
        # 결합만 끊고 주변 분포는 유지 (임계 안쪽 상관 붕괴)
        follower[broken_from:] = rng.normal(0, 1, size=(n_ticks - broken_from, 1))
    return np.concatenate([base, follower, rng.normal(0, 1, size=(n_ticks, 2))], axis=1)


def _var_loaded_model(rng: np.random.Generator, window=12, stride=6, k=2) -> LoadedModel:
    # 지연 결합 정상으로 재구성·VAR 두 경로를 함께 적합 (BE_ANOM01_VAR01)
    train = sliding_windows(_lagged_normal(rng, 6000), window, stride)
    model = PcaMspc(0.9, 0.999, cross_correlation=True).fit(train)
    limit = float(np.quantile(ewma(np.minimum(model.score(train), 3.0), 0.1), 0.999))
    var_model = VarResidual(lag=2, quantile=0.999).fit(train)
    var_limit = float(np.quantile(ewma(np.minimum(var_model.score(train), 3.0), 0.1), 0.999))
    return LoadedModel(
        model,
        window,
        stride,
        k,
        0.1,
        3.0,
        max(limit, 1e-12),
        transition_threshold=1000.0,
        transition_settle=10,
        var_model=var_model,
        var_ewma_limit=max(var_limit, 1e-12),
    )


def test_var_path_raises_score_on_lagged_correlation_break():
    # 지연 결합 소실은 VAR 경로가 포착, 병합 점수가 재구성 단독보다 높아짐
    rng = np.random.default_rng(1)
    loaded = _var_loaded_model(rng)
    series = _lagged_normal(rng, 300, broken_from=150)

    with_var = AnomalyScorer({"Etching": loaded})
    without_var = AnomalyScorer({"Etching": replace(loaded, var_model=None)})
    merged = with_var.score_latest("Etching", EQP, series)
    baseline = without_var.score_latest("Etching", EQP, series)
    assert merged.fired is True
    assert merged.score > baseline.score


def test_var_path_does_not_fire_on_normal():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _var_loaded_model(rng)})
    result = scorer.score_latest("Etching", EQP, _lagged_normal(rng, 300))
    assert result is not None and result.fired is False


def test_unknown_process_type_returns_none():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    assert scorer.score_latest("Unknown", EQP, _coupled_normal(rng, 300)) is None


def test_insufficient_window_returns_none():
    rng = np.random.default_rng(1)
    scorer = AnomalyScorer({"Etching": _loaded_model(rng)})
    # 윈도우(12)보다 짧은 시계열 → None
    assert scorer.score_latest("Etching", EQP, _coupled_normal(rng, 5)) is None
