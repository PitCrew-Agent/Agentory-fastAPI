"""EXP-012 마스킹 조건부 복원 vs 재구성 PCA vs VAR (BE_ANOM01_MASK02)

실행: uv run python experiments/exp_012_conditional_recon.py
선형·비선형 연쇄 합성에서 상관 붕괴 감지·기여도를 비교
- 분리 배율: main etch 상관붕괴 점수 중앙값 / 정상 점수 중앙값 (높을수록 잘 분리)
- 유예 60틱 recall: EWMA·지속 K 후처리 후 감지율
- 기여도 적중: 발령 윈도우 top_channel이 깨진 쌍(gas·pressure)에 속하는 비율

가설: 마스킹 조건부 복원은 변수별 연쇄 학습을 강제해 기여도가 정확, MLP의 비선형 이득은
연쇄가 비선형일 때만 나타남
"""

import numpy as np

from anomaly.models.conditional_recon import ConditionalReconstruction
from anomaly.models.pca_mspc import PcaMspc
from anomaly.models.var_residual import VarResidual
from anomaly.scoring import ewma, sustained
from anomaly.synthetic_step import WAFER_TICKS, generate_stepped_rows
from anomaly.windowing import sliding_windows
from simulator.generator import VARS

WINDOW, STRIDE, K, ALPHA, CLIP, Q = 12, 6, 2, 0.15, 3.0, 0.999
ONSET = WAFER_TICKS * 2
BROKEN_PAIR = (VARS.index("gas_flow"), VARS.index("pressure"))


def _main_windows(scenario, nonlinear, tick_min=0, n_wafers=10):
    rows = generate_stepped_rows(
        "E", scenario, WAFER_TICKS * n_wafers, ONSET, seed=1, nonlinear=nonlinear
    )
    # main etch 정착 구간만 (스텝 인지 전제), 첫 연속 구간들 이어붙이지 않고 tick 필터
    rows = [r for r in rows if r["step"] == "main_etch" and r["tick"] >= tick_min]
    mat = np.array([[r[v] for v in VARS] for r in rows], dtype=float)
    return sliding_windows(mat, WINDOW, STRIDE)


def _limit(model, train_w):
    return max(float(np.quantile(ewma(np.minimum(model.score(train_w), CLIP), ALPHA), Q)), 1e-12)


def _evaluate(model, train_w, normal_w, corr_w):
    limit = _limit(model, train_w)
    normal_med = float(np.median(model.score(normal_w)))
    corr_med = float(np.median(model.score(corr_w)))
    fired = sustained(ewma(np.minimum(model.score(corr_w), CLIP), ALPHA) / limit > 1.0, K)
    recall = float(fired.any())
    if hasattr(model, "top_channel") and fired.any():
        tc = model.top_channel(corr_w[fired])
        attribution = float(np.isin(tc, BROKEN_PAIR).mean())
    else:
        attribution = float("nan")
    ratio = corr_med / normal_med if normal_med > 0 else float("inf")
    return ratio, recall, attribution


def _detectors():
    return {
        "PCA both": lambda: PcaMspc(0.9, Q, cross_correlation=True),
        "PCA SPE": lambda: PcaMspc(0.9, Q, cross_correlation=True, statistic="spe"),
        "VAR": lambda: VarResidual(lag=2, quantile=Q),
        "조건부복원 linear": lambda: ConditionalReconstruction("linear", Q),
        "조건부복원 mlp": lambda: ConditionalReconstruction("mlp", Q),
    }


def main() -> None:
    # 선형 연쇄에서 전체 붕괴(포화)와 약한 붕괴(난도 상승) 비교
    for scn, title in (("corr_break_main", "전체 붕괴"), ("corr_break_weak", "약한 붕괴")):
        train_w = _main_windows("normal", False)
        normal_w = _main_windows("normal", False, tick_min=ONSET)
        corr_w = _main_windows(scn, False, tick_min=ONSET)
        print(f"\n=== {title} (main etch, 스텝 인지 전제) ===")
        print(f"{'검출기':18}{'분리배율':>9}{'recall':>8}{'기여도적중':>11}")
        for name, factory in _detectors().items():
            model = factory().fit(train_w)
            ratio, recall, attr = _evaluate(model, train_w, normal_w, corr_w)
            attr_s = "-" if attr != attr else f"{attr:.2f}"
            print(f"{name:18}{ratio:>9.1f}{recall:>8.2f}{attr_s:>11}")


if __name__ == "__main__":
    main()
