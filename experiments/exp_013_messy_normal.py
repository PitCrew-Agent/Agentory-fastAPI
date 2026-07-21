"""EXP-013 지저분한 정상에서 검출기 오탐 견고성 (BE_ANOM01_MESSY01)

실행: uv run python experiments/exp_013_messy_normal.py
EXP-011·012는 정상이 너무 깨끗해 어떤 검출기든 recall 1.00에 도달, 검출기 차이가 헤드라인
지표로 드러나지 않았음. 실측 정상의 지저분함(런투런 setpoint 변동·센서 글리치)을 합성에
주입해 검출기 간 오탐 견고성을 변별

가설: 관계 기반 검출기(SPE·VAR·조건부복원)는 양성 마진 변동에 강건해 오탐이 낮고, 크기 기반
(T²)은 학습 때 못 본 양성 모드에서 오탐이 뜬다. train/test를 다른 웨이퍼로 나눠 모드 불일치
일반화를 검증

- 오탐율: 학습 때 못 본 정상 윈도우가 EWMA·지속 K 후 발령하는 비율 (낮을수록 견고)
- recall: 상관붕괴 감지율 (유지돼야 함)
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


def _windows(scenario, messy, wafer_lo, wafer_hi, onset_wafers=0):
    # wafer_lo~wafer_hi 구간만 생성(웨이퍼별 양성 모드가 구간마다 달라 train/test 모드 불일치)
    n = WAFER_TICKS * wafer_hi
    rows = generate_stepped_rows(
        "E", scenario, n, WAFER_TICKS * onset_wafers, seed=1, messy=messy
    )
    rows = [r for r in rows if wafer_lo * WAFER_TICKS <= r["tick"] < wafer_hi * WAFER_TICKS]
    rows = [r for r in rows if r["step"] == "main_etch"]
    mat = np.array([[r[v] for v in VARS] for r in rows], dtype=float)
    return sliding_windows(mat, WINDOW, STRIDE)


def _limit(model, train_w):
    scored = ewma(np.minimum(model.score(train_w), CLIP), ALPHA)
    return max(float(np.quantile(scored, Q)), 1e-12)


def _ratios(model, limit, windows):
    return ewma(np.minimum(model.score(windows), CLIP), ALPHA) / limit


def _fire_rate(model, limit, windows):
    return float(sustained(_ratios(model, limit, windows) > 1.0, K).mean())


def _detectors():
    return {
        "PCA T² (크기)": lambda: PcaMspc(0.9, Q, statistic="t2"),
        "PCA SPE (관계)": lambda: PcaMspc(0.9, Q, cross_correlation=True, statistic="spe"),
        "PCA both": lambda: PcaMspc(0.9, Q, cross_correlation=True),
        "VAR (관계)": lambda: VarResidual(lag=2, quantile=Q),
        "조건부복원 (관계)": lambda: ConditionalReconstruction("linear", Q),
    }


def _run(messy):
    train = _windows("normal", messy, 0, TRAIN_WAFERS)
    normal_test = _windows("normal", messy, TRAIN_WAFERS, TRAIN_WAFERS + TEST_WAFERS)
    # 상관붕괴는 test 구간 후반부 main etch에서 발생
    hi = TRAIN_WAFERS + TEST_WAFERS
    corr = _windows("corr_break_main", messy, TRAIN_WAFERS, hi, onset_wafers=TRAIN_WAFERS)
    label = "지저분한 정상" if messy else "깨끗한 정상"
    print(f"\n=== {label} (train {TRAIN_WAFERS}웨이퍼 / test 다른 {TEST_WAFERS}웨이퍼) ===")
    print(f"{'검출기':20}{'정상 오탐율':>12}{'상관붕괴 최대배율':>16}{'감지':>8}")
    for name, factory in _detectors().items():
        model = factory().fit(train)
        limit = _limit(model, train)
        fa = _fire_rate(model, limit, normal_test)
        peak = float(_ratios(model, limit, corr).max())  # 1.0 초과여야 감지 가능
        detected = "감지" if _fire_rate(model, limit, corr) > 0 else "미감지"
        print(f"{name:20}{fa:>12.2f}{peak:>16.2f}{detected:>8}")


def main() -> None:
    _run(messy=False)
    _run(messy=True)


if __name__ == "__main__":
    main()
