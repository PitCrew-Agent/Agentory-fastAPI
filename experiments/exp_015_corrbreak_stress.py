"""EXP-015 관계 붕괴(corr_break) 검출 강건성 스트레스 (BE_ANOM01_CB15)

실행: uv run python experiments/exp_015_corrbreak_stress.py

배경
- 고정 평가 세트 v2는 corr_break 세그먼트가 2건(SYN-B01/B02)뿐이라, 통계 recall 1.0·
  규칙 recall 0.0이 표본 2건에 기댄 값. 규칙 0.0을 "관계 붕괴를 구조적으로 못 잡음"으로
  일반화하면 재현 시 무너질 위험
- eval_set의 rng = Random(f"{seed}:{equipment_id}") 구조를 이용해 장비 ID·base seed만
  바꿔 동일 시나리오의 독립 실현 다수를 생성, 검출률 분포를 측정

설계
- corr_break 80건 + 정상 4건을 한 base seed당 생성, base seed 4개(총 320 corr_break)
- 채점은 서빙 최종 구성(exp_008_suppress)과 동일, 규칙은 exp_004_rule과 동일(유예 60 tick)
- 학습 세트·모델 적합은 v2 train 설정 재사용(EtchingCoupled 정상)
- 결과 스냅샷을 experiments/results/exp_015_corrbreak_stress.json에 기록
"""

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

import anomaly.eval_set as es
from anomaly.evaluation import auc_pr, point_labels, summarize
from anomaly.models.pca_mspc import PcaMspc
from anomaly.rule_baseline import rule_detections
from anomaly.scoring import ewma, ewma_masked, sustained, transition_mask
from anomaly.windowing import sliding_windows, window_starts
from simulator.generator import VARS

RESULTS = Path("experiments/results/exp_015_corrbreak_stress.json")
V2_MANIFEST = Path("data/eval/v2/manifest.json")

SEEDS = [20260716, 11111, 77777, 20260101]
N_CB = 80  # base seed당 corr_break 세그먼트 수
N_NORMAL = 4  # FAR 참고용 정상 세그먼트 수

# 서빙 최종 구성 (exp_008_suppress), 통계 채점
STAT = dict(
    window=12,
    stride=6,
    n_components=0.9,
    threshold_quantile=0.999,
    cross_correlation=True,
    ewma_alpha=0.1,
    ewma_clip=3.0,
    confirm_k=2,
    max_delay_ticks=180,
    transition_threshold=1000.0,
    transition_settle=10,
)
RULE_MAX_DELAY = 60  # exp_004_rule과 동일 유예


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _clip(scores: np.ndarray, upper: float | None) -> np.ndarray:
    return np.minimum(scores, upper) if upper else scores


def _build(out_dir: Path, seed: int) -> dict:
    """base seed 하나에 대해 corr_break·정상 세그먼트 생성"""
    train_cfg = json.loads(V2_MANIFEST.read_text())["config"]["train"]
    segments = [
        dict(
            equipment_id=f"SYN-N{i:03d}",
            process_type="EtchingCoupled",
            scenario="normal",
            generator="synthetic",
        )
        for i in range(1, N_NORMAL + 1)
    ] + [
        dict(
            equipment_id=f"SYN-B{i:03d}",
            process_type="EtchingCoupled",
            scenario="corr_break",
            generator="synthetic",
        )
        for i in range(1, N_CB + 1)
    ]
    cfg = dict(
        version="corrbreak_stress",
        seed=seed,
        tick_seconds=5.0,
        train=train_cfg,
        eval=dict(n_ticks=4320, drift_start_tick=720, segments=segments),
    )
    es.build_eval_set(cfg, out_dir)
    return cfg


def _score_stat(root: Path) -> tuple[dict, dict, list, float]:
    """서빙 구성으로 채점, run._run_windowed 로직 복제 (mlflow 비의존)"""
    train = es.load_train_frame(root)
    frame = es.load_eval_frame(root)
    events = es.load_events(root)
    mcfg = es.load_manifest(root)["config"]
    tick_seconds = mcfg["tick_seconds"]
    window, stride = STAT["window"], STAT["stride"]

    eval_types = {s["equipment_id"]: s["process_type"] for s in mcfg["eval"]["segments"]}
    train_types = {e["equipment_id"]: e["process_type"] for e in mcfg["train"]["equipments"]}

    def train_windows(eq: str) -> np.ndarray:
        series = train[train["equipment_id"] == eq].sort_values("tick")
        return sliding_windows(series[list(VARS)].to_numpy(), window, stride)

    models, limits = {}, {}
    for ptype in sorted(set(train_types.values())):
        parts = [train_windows(eq) for eq, t in train_types.items() if t == ptype]
        model = PcaMspc(
            STAT["n_components"], STAT["threshold_quantile"], STAT["cross_correlation"]
        ).fit(np.concatenate(parts))
        models[ptype] = model
        acc = np.concatenate(
            [ewma(_clip(model.score(p), STAT["ewma_clip"]), STAT["ewma_alpha"]) for p in parts]
        )
        limits[ptype] = max(float(np.quantile(acc, STAT["threshold_quantile"])), 1e-12)

    detections, n_ticks, scores_all, labels_all = {}, {}, [], []
    for eq, group in frame.groupby("equipment_id"):
        ordered = group.sort_values("tick")
        n = len(ordered)
        n_ticks[str(eq)] = n
        windows = sliding_windows(ordered[list(VARS)].to_numpy(), window, stride)
        ends = window_starts(n, window, stride) + window - 1
        raw = models[eval_types[str(eq)]].score(windows)
        mask = transition_mask(raw, STAT["transition_threshold"], STAT["transition_settle"])
        scores = (
            ewma_masked(_clip(raw, STAT["ewma_clip"]), STAT["ewma_alpha"], mask)
            / limits[eval_types[str(eq)]]
        )
        fired = sustained(scores > 1.0, STAT["confirm_k"]) & ~mask
        detections[str(eq)] = ends[fired]
        latest = np.clip(np.searchsorted(ends, np.arange(n), side="right") - 1, 0, None)
        scores_all.append(scores[latest])
        labels_all.append(point_labels(str(eq), n, events))

    metrics = summarize(events, detections, n_ticks, tick_seconds, STAT["max_delay_ticks"])
    metrics["auc_pr"] = auc_pr(np.concatenate(scores_all), np.concatenate(labels_all))
    return metrics, detections, events, tick_seconds


def _cb_recall(events: list, detections: dict, max_delay: int) -> tuple[int, int]:
    cb = [e for e in events if e.kind == "corr_break"]
    hit = 0
    for e in cb:
        ticks = detections.get(e.equipment_id, np.array([]))
        if np.any((ticks >= e.start_tick) & (ticks <= e.start_tick + max_delay)):
            hit += 1
    return hit, len(cb)


def main() -> None:
    per_seed = []
    stat_hit = stat_tot = rule_hit = rule_tot = 0
    aucs, delays = [], []
    for seed in SEEDS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build(root, seed)
            metrics, detections, events, _ = _score_stat(root)
            frame = es.load_eval_frame(root)
            rule_det = rule_detections(frame)
            sh, st = _cb_recall(events, detections, STAT["max_delay_ticks"])
            rh, rt = _cb_recall(events, rule_det, RULE_MAX_DELAY)
            stat_hit += sh
            stat_tot += st
            rule_hit += rh
            rule_tot += rt
            aucs.append(metrics["auc_pr"])
            if metrics.get("delay_mean_corr_break_ticks") is not None:
                delays.append(float(metrics["delay_mean_corr_break_ticks"]))
            per_seed.append(
                dict(
                    seed=seed,
                    stat_detected=sh,
                    rule_detected=rh,
                    total=st,
                    stat_recall=round(sh / st, 4),
                    rule_recall=round(rh / rt, 4),
                    auc_pr=round(metrics["auc_pr"], 4),
                )
            )
            print(
                f"seed {seed}: 통계 {sh}/{st}={sh / st:.3f}  규칙 {rh}/{rt}={rh / rt:.3f}  "
                f"AUC-PR {metrics['auc_pr']:.4f}"
            )

    result = dict(
        experiment="EXP-015-CORRBREAK-STRESS",
        run_at=datetime.now(UTC).isoformat(),
        git_commit=_git_commit(),
        eval_set_version="corrbreak_stress",
        config=dict(
            seeds=SEEDS,
            n_corr_break_per_seed=N_CB,
            n_normal_per_seed=N_NORMAL,
            stat=STAT,
            rule_max_delay_ticks=RULE_MAX_DELAY,
        ),
        metrics=dict(
            corr_break_total=stat_tot,
            stat_recall_corr_break=round(stat_hit / stat_tot, 4),
            rule_recall_corr_break=round(rule_hit / rule_tot, 4),
            stat_over_rule=round((stat_hit / stat_tot) / (rule_hit / rule_tot), 2),
            stat_detected=stat_hit,
            rule_detected=rule_hit,
            auc_pr_mean=round(float(np.mean(aucs)), 4),
            delay_mean_corr_break_ticks=round(float(np.mean(delays)), 1) if delays else None,
        ),
        per_seed=per_seed,
    )
    RESULTS.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    m = result["metrics"]
    print(
        f"\n[합계 {m['corr_break_total']}건] 통계 recall {m['stat_recall_corr_break']} · "
        f"규칙 recall {m['rule_recall_corr_break']} · 통계/규칙 {m['stat_over_rule']}배 · "
        f"AUC-PR {m['auc_pr_mean']}"
    )
    print(f"기록: {RESULTS}")


if __name__ == "__main__":
    main()
