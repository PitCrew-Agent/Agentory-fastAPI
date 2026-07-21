"""EXP-011 스텝 무시 vs 스텝 인지 이상 감지 비교 (BE_ANOM01_STEP01)

실행: uv run python experiments/exp_011_step_aware.py
스텝 구조 합성으로 세 방식 비교
- agnostic: 통짜 PCA (스텝 무시), 전이 억제 없음
- agnostic+suppress: 통짜 PCA + 전이 억제 (현행 서빙 방식)
- step-aware: 스텝별 PCA·한계 (FDC 정석)

지표: 정상 세그먼트 오탐/설비·일, main etch 상관 붕괴 감지율·지연, 스텝 지연 감지
"""

import numpy as np

from anomaly.models.pca_mspc import PcaMspc
from anomaly.scoring import ewma, ewma_masked, sustained, transition_mask
from anomaly.synthetic_step import RECIPE_STEPS, WAFER_TICKS, generate_stepped_rows
from anomaly.windowing import sliding_windows, window_starts
from simulator.generator import VARS

WINDOW, STRIDE, K = 12, 6, 2
ALPHA, CLIP, Q = 0.1, 3.0, 0.999
TICK_SECONDS = 5.0
STEP_NAMES = [s[0] for s in RECIPE_STEPS]


def _matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    mat = np.array([[r[v] for v in VARS] for r in rows], dtype=float)
    steps = np.array([r["step"] for r in rows])
    return mat, steps


def _window_steps(steps: np.ndarray, n: int) -> np.ndarray:
    # 윈도우 전체가 한 스텝이면 그 스텝, 경계를 걸치면 transition (스케줄로 경계 인지)
    starts = window_starts(n, WINDOW, STRIDE)
    out = np.empty(starts.shape, dtype=object)
    for i, s in enumerate(starts):
        seg = steps[s : s + WINDOW]
        out[i] = seg[0] if (seg == seg[0]).all() else "transition"
    return out.astype(str)


def _limit(scores: np.ndarray, mask: np.ndarray | None = None) -> float:
    acc = (
        ewma_masked(np.minimum(scores, CLIP), ALPHA, mask)
        if mask is not None
        else ewma(np.minimum(scores, CLIP), ALPHA)
    )
    warm = acc[40:] if acc.size > 140 else acc
    return max(float(np.quantile(warm, Q)), 1e-12)


def _fit_pca(windows: np.ndarray) -> PcaMspc:
    return PcaMspc(0.9, Q, cross_correlation=True).fit(windows)


RUN_WARMUP = 3  # 스텝 인스턴스 진입 EWMA 정착 구간, 판정·한계에서 제외


def _step_runs(wsteps: np.ndarray) -> list[tuple[int, int, str]]:
    # 동일 스텝 연속 구간(웨이퍼별 스텝 인스턴스) 분해
    runs, i = [], 0
    while i < len(wsteps):
        j = i
        while j < len(wsteps) and wsteps[j] == wsteps[i]:
            j += 1
        runs.append((i, j, wsteps[i]))
        i = j
    return runs


def _fit_step_limits(windows_per_eq, wsteps_per_eq, models, alpha=ALPHA) -> dict[str, float]:
    # 스텝 인스턴스마다 EWMA를 새로 돌려(경계 너머 누적 방지) 분위수로 한계 산출
    pools: dict[str, list] = {s: [] for s in STEP_NAMES}
    for w, ws in zip(windows_per_eq, wsteps_per_eq, strict=True):
        for a, b, step in _step_runs(ws):
            if step in models and b - a > RUN_WARMUP:
                acc = ewma(np.minimum(models[step].score(w[a:b]), CLIP), alpha)
                pools[step].append(acc[RUN_WARMUP:])
    return {s: max(float(np.quantile(np.concatenate(p), Q)), 1e-12) for s, p in pools.items() if p}


def _score_agnostic(model, limit, windows, suppress):
    raw = model.score(windows)
    mask = transition_mask(raw, 1000.0, 10) if suppress else np.zeros(raw.shape, bool)
    norm = ewma_masked(np.minimum(raw, CLIP), ALPHA, mask) / limit
    return sustained(norm > 1.0, K) & ~mask


def _score_step_aware(models, limits, windows, wsteps, alpha=ALPHA):
    # 스텝 인스턴스마다 EWMA 새로 시작, 정착 구간 미발령 (경계 너머 누적 방지)
    fired = np.zeros(windows.shape[0], bool)
    for a, b, step in _step_runs(wsteps):
        if step not in models or step not in limits:
            continue
        raw = models[step].score(windows[a:b])
        norm = ewma(np.minimum(raw, CLIP), alpha) / limits[step]
        run_fired = sustained(norm > 1.0, K)
        run_fired[:RUN_WARMUP] = False
        fired[a:b] = run_fired
    return fired


def main() -> None:
    rng_seed = 20260720
    # 학습: 정상 웨이퍼 다수
    train_rows = []
    for i in range(4):
        train_rows.append(
            generate_stepped_rows(f"TRN-{i}", "normal", WAFER_TICKS * 20, 0, rng_seed)
        )
    # 평가: 정상 2 + 상관붕괴 2 + 스텝지연 2, onset 2웨이퍼 후
    onset = WAFER_TICKS * 2
    eval_specs = [
        ("EVL-N1", "normal"),
        ("EVL-N2", "normal"),
        ("EVL-B1", "corr_break_main"),
        ("EVL-B2", "corr_break_main"),
        ("EVL-S1", "step_delay"),
        ("EVL-S2", "step_delay"),
    ]
    eval_rows = {
        eid: generate_stepped_rows(eid, scn, WAFER_TICKS * 8, onset, rng_seed)
        for eid, scn in eval_specs
    }

    # 학습 윈도우, 스텝별 분리
    train_windows_all, train_wsteps = [], []
    for rows in train_rows:
        mat, steps = _matrix(rows)
        w = sliding_windows(mat, WINDOW, STRIDE)
        train_windows_all.append(w)
        train_wsteps.append(_window_steps(steps, len(rows)))
    tw = np.concatenate(train_windows_all)
    tws = np.concatenate(train_wsteps)

    # 통짜 모델
    agn_model = _fit_pca(tw)
    agn_limit = _limit(agn_model.score(tw))
    agn_limit_sup = _limit(agn_model.score(tw), transition_mask(agn_model.score(tw), 1000.0, 10))
    # 스텝별 모델 (부분공간은 스텝 전체로 적합), 한계는 alpha 스윕에서 인스턴스별 산출
    step_models = {}
    for step in STEP_NAMES:
        idx = np.flatnonzero(tws == step)
        if idx.size >= 200:
            step_models[step] = _fit_pca(tw[idx])

    print(f"{'방식':22} {'정상오탐/설비일':>14} {'상관붕괴recall':>14} {'스텝지연recall':>14}")
    print("-" * 68)

    def evaluate(scorer_name, score_fn):
        fa_windows, days = 0, 0.0
        corr_hits, corr_total = 0, 0
        step_hits, step_total = 0, 0
        for eid, scn in eval_specs:
            rows = eval_rows[eid]
            mat, steps = _matrix(rows)
            w = sliding_windows(mat, WINDOW, STRIDE)
            ends = window_starts(len(rows), WINDOW, STRIDE) + WINDOW - 1
            wsteps = _window_steps(steps, len(rows))
            fired = score_fn(w, wsteps)
            days += len(rows) * TICK_SECONDS / 86400
            if scn == "normal":
                fa_windows += int(fired.sum())
            elif scn == "corr_break_main":
                # 이상 구간: onset 이후 main_etch 윈도우
                region = (ends >= onset) & (wsteps == "main_etch")
                corr_total += 1
                if (fired & region).any():
                    corr_hits += 1
                fa_windows += int((fired & ~region & (ends < onset)).sum())
            elif scn == "step_delay":
                region = ends >= onset
                step_total += 1
                if (fired & region).any():
                    step_hits += 1
        fa_day = fa_windows / days
        cr = corr_hits / corr_total if corr_total else float("nan")
        sr = step_hits / step_total if step_total else float("nan")
        print(f"{scorer_name:22} {fa_day:>14.2f} {cr:>14.2f} {sr:>14.2f}")

    evaluate("agnostic", lambda w, ws: _score_agnostic(agn_model, agn_limit, w, False))
    evaluate("agnostic+suppress", lambda w, ws: _score_agnostic(agn_model, agn_limit_sup, w, True))
    print("--- step-aware alpha 스윕 (스텝별 좁은 baseline은 빠른 누적 감당) ---")
    for alpha in (0.1, 0.15, 0.2, 0.3):
        lims = _fit_step_limits(train_windows_all, train_wsteps, step_models, alpha)
        evaluate(
            f"step-aware a={alpha}",
            lambda w, ws, al=alpha, lm=lims: _score_step_aware(step_models, lm, w, ws, al),
        )


if __name__ == "__main__":
    main()
