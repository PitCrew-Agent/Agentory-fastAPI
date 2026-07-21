"""EXP-014 지저분함 강도 스윕, 검출기 견고성 경계 (BE_ANOM01_MESSY02)

실행: uv run python experiments/exp_014_messy_sweep.py
EXP-013은 기준 강도(messy_scale 1.0) 한 점에서 크기 vs 관계 검출기를 변별했음. 실측 배포
전 알아야 할 것은 지저분함이 얼마나 심해지면 각 검출기가 무너지는가의 견고성 경계. messy_scale
을 스윕해 오탐율과 상관붕괴 감지의 붕괴점을 그림

가설: 크기 기반(T²)은 낮은 강도에서 상관붕괴 민감도를 잃고(baseline 팽창), 관계 기반
(SPE·VAR·조건부복원)은 높은 강도까지 감지를 유지. 강도가 매우 커지면 관계 기반도 결국
오탐이 상승
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
TRAIN_WAFERS, TEST_WAFERS = 30, 20
SCALES = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0)


def _windows(scenario, scale, wafer_lo, wafer_hi, onset_wafers=0):
    rows = generate_stepped_rows(
        "E", scenario, WAFER_TICKS * wafer_hi, WAFER_TICKS * onset_wafers,
        seed=1, messy=True, messy_scale=scale,
    )
    rows = [
        r
        for r in rows
        if wafer_lo * WAFER_TICKS <= r["tick"] < wafer_hi * WAFER_TICKS and r["step"] == "main_etch"
    ]
    mat = np.array([[r[v] for v in VARS] for r in rows], dtype=float)
    return sliding_windows(mat, WINDOW, STRIDE)


def _limit(model, train_w):
    return max(float(np.quantile(ewma(np.minimum(model.score(train_w), CLIP), ALPHA), Q)), 1e-12)


def _fire_rate(model, limit, windows):
    ratios = ewma(np.minimum(model.score(windows), CLIP), ALPHA) / limit
    return float(sustained(ratios > 1.0, K).mean())


def _detectors():
    return {
        "T² (크기)": lambda: PcaMspc(0.9, Q, statistic="t2"),
        "SPE (관계)": lambda: PcaMspc(0.9, Q, cross_correlation=True, statistic="spe"),
        "VAR (관계)": lambda: VarResidual(lag=2, quantile=Q),
        "조건부복원 (관계)": lambda: ConditionalReconstruction("linear", Q),
    }


def main() -> None:
    hi = TRAIN_WAFERS + TEST_WAFERS
    print("각 셀 = 정상 오탐율 / 상관붕괴 감지(O·X), 강도별 견고성 경계")
    header = "".join(f"{f's{s}':>16}" for s in SCALES)
    print(f"{'검출기':18}{header}")
    for name, factory in _detectors().items():
        cells = []
        for scale in SCALES:
            train = _windows("normal", scale, 0, TRAIN_WAFERS)
            normal_test = _windows("normal", scale, TRAIN_WAFERS, hi)
            corr = _windows("corr_break_main", scale, TRAIN_WAFERS, hi, onset_wafers=TRAIN_WAFERS)
            model = factory().fit(train)
            limit = _limit(model, train)
            fa = _fire_rate(model, limit, normal_test)
            detected = "O" if _fire_rate(model, limit, corr) > 0 else "X"
            cells.append(f"{fa:.2f}/{detected}")
        print(f"{name:18}" + "".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
