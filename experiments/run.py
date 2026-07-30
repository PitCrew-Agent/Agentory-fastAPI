"""실험 실행·기록 공용 러너

실행: uv run python experiments/run.py --config experiments/configs/exp_001_rule_baseline.yaml
채점은 anomaly.evaluation.summarize 단일 소스, 기록은 3층 구조
- mlruns/          MLflow 로컬 (전체 원본, git 제외)
- experiments/results/<실험명>.json  확정 지표 스냅샷 (git 커밋, 기계 판독 가능)
- docs/experiments/EXP-NNN-*.md      해석·결론 (git 커밋, 수동 작성)
"""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml

from anomaly.eval_set import load_eval_frame, load_events, load_manifest, load_train_frame
from anomaly.evaluation import auc_pr, point_labels, summarize
from anomaly.models.pca_mspc import PcaMspc
from anomaly.rule_baseline import equipment_n_ticks, rule_detections, rule_point_scores
from anomaly.scoring import ewma, ewma_masked, sustained, transition_mask
from anomaly.windowing import sliding_windows, window_starts
from simulator.generator import VARS

MLFLOW_EXPERIMENT = "anomaly-detection"
RESULTS_DIR = Path("experiments/results")


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _clip(scores: np.ndarray, upper: float | None) -> np.ndarray:
    # EWMA 입력 winsorize, 미지정 시 원 점수 유지
    return np.minimum(scores, upper) if upper else scores


def run_rule_baseline(config: dict) -> dict[str, float]:
    """EXP-001 규칙 레이어 단독 채점, 평가 세트 저장 컬럼만 사용 (재실행 없음)"""
    root = Path(config["eval_set"])
    frame = load_eval_frame(root)
    events = load_events(root)
    tick_seconds = load_manifest(root)["config"]["tick_seconds"]

    metrics = summarize(
        events,
        rule_detections(frame),
        equipment_n_ticks(frame),
        tick_seconds,
        config.get("max_delay_ticks"),
    )
    scores, labels = rule_point_scores(frame, events)
    metrics["auc_pr"] = auc_pr(scores, labels)
    return metrics


def _run_windowed(config: dict, make_model) -> dict[str, float]:
    """윈도우 기반 detector 공용 채점 루프

    make_model()이 AnomalyDetector를 반환, 공정 유형별로 정상 학습 세트에 적합
    판정 tick은 윈도우 끝, 포인트 점수는 각 tick까지 완료된 최신 윈도우 점수 할당
    (첫 윈도우 이전 구간은 첫 점수)
    ewma_alpha 지정 시 점수 시계열에 EWMA 적용 + 학습 정상 시퀀스로 한계 재캘리브레이션
    (EXP-006, 지속 저강도 신호 누적 감지)
    """
    root = Path(config["eval_set"])
    train = load_train_frame(root)
    frame = load_eval_frame(root)
    events = load_events(root)
    manifest_cfg = load_manifest(root)["config"]
    tick_seconds = manifest_cfg["tick_seconds"]
    window, stride = config["window"], config["stride"]
    ewma_alpha = config.get("ewma_alpha")
    # EWMA 전 원 점수 상한 (winsorize), 급성·모드 전이의 거대 점수가 느린 감쇠로
    # 수백 tick 오탐 꼬리를 만드는 것을 차단 (지속 저강도 신호 누적에는 영향 없음)
    ewma_clip = config.get("ewma_clip")
    confirm_k = config.get("confirm_k", 1)  # 연속 K윈도우 초과 확인 규칙 (EXP-007)
    # 전이/정착 억제 (EXP-008), raw 점수 급등 구간을 EWMA·발령에서 제외 (모드 전이 오탐)
    transition_threshold = config.get("transition_threshold", 0.0)
    transition_settle = config.get("transition_settle", 0)

    # 설비 → 공정 유형 매핑은 manifest의 config 스냅샷에서 복원 (평가 세트 재생성 불필요)
    eval_types = {s["equipment_id"]: s["process_type"] for s in manifest_cfg["eval"]["segments"]}
    train_types = {
        e["equipment_id"]: e["process_type"] for e in manifest_cfg["train"]["equipments"]
    }

    def train_windows(equipment_id: str) -> np.ndarray:
        series = train[train["equipment_id"] == equipment_id].sort_values("tick")
        return sliding_windows(series[list(VARS)].to_numpy(), window, stride)

    models: dict[str, object] = {}
    ewma_limits: dict[str, float] = {}
    for ptype in sorted(set(train_types.values())):
        equipment_ids = [eq for eq, t in train_types.items() if t == ptype]
        parts = [train_windows(eq) for eq in equipment_ids]
        model = make_model().fit(np.concatenate(parts))
        models[ptype] = model
        if ewma_alpha:
            # 설비별 정상 점수 시퀀스에 동일 클리핑+EWMA 적용 후 분위수로 한계 재산출
            accumulated = np.concatenate(
                [ewma(_clip(model.score(part), ewma_clip), ewma_alpha) for part in parts]
            )
            ewma_limits[ptype] = max(
                float(np.quantile(accumulated, config["threshold_quantile"])), 1e-12
            )

    detections: dict[str, np.ndarray] = {}
    n_ticks: dict[str, int] = {}
    scores_all: list[np.ndarray] = []
    labels_all: list[np.ndarray] = []
    for equipment_id, group in frame.groupby("equipment_id"):
        ordered = group.sort_values("tick")
        n = len(ordered)
        n_ticks[str(equipment_id)] = n
        windows = sliding_windows(ordered[list(VARS)].to_numpy(), window, stride)
        ends = window_starts(n, window, stride) + window - 1
        ptype = eval_types[str(equipment_id)]
        raw = models[ptype].score(windows)
        mask = transition_mask(raw, transition_threshold, transition_settle)
        if ewma_alpha:
            accumulated = ewma_masked(_clip(raw, ewma_clip), ewma_alpha, mask)
            scores = accumulated / ewma_limits[ptype]
        else:
            scores = raw
        fired = sustained(scores > 1.0, confirm_k) & ~mask  # 전이 구간 발령 제외
        detections[str(equipment_id)] = ends[fired]
        latest = np.clip(np.searchsorted(ends, np.arange(n), side="right") - 1, 0, None)
        scores_all.append(scores[latest])
        labels_all.append(point_labels(str(equipment_id), n, events))

    metrics = summarize(events, detections, n_ticks, tick_seconds, config.get("max_delay_ticks"))
    metrics["auc_pr"] = auc_pr(np.concatenate(scores_all), np.concatenate(labels_all))
    return metrics


def run_pca_mspc(config: dict) -> dict[str, float]:
    """EXP-002~004 PCA MSPC (T²+SPE) 채점, statistic으로 기여 분해도 지원 (EXP-009)"""
    return _run_windowed(
        config,
        lambda: PcaMspc(
            config["n_components"],
            config["threshold_quantile"],
            config.get("cross_correlation", False),
            config.get("statistic", "both"),
        ),
    )


def _make_detector(config: dict):
    """detector 키로 비교군 인스턴스 생성 (EXP-009 계열 비교)

    전 검출기는 score()가 한계 정규화 값(1.0 초과=이상)을 반환하는 계약을 지켜
    동일 EWMA·지속 K 후처리와 동일 지표로 비교 가능
    """
    kind = config["detector"]
    quantile = config["threshold_quantile"]
    xcorr = config.get("cross_correlation", True)
    seed = config.get("seed", 0)
    if kind == "var":
        from anomaly.models.var_residual import VarResidual

        return VarResidual(lag=config.get("var_lag", 2), quantile=quantile)
    if kind == "kernel_pca":
        from anomaly.models.kernel_pca import KernelPcaResidual

        return KernelPcaResidual(
            n_components=config.get("kpca_components", 12),
            gamma=config.get("kpca_gamma"),
            quantile=quantile,
            cross_correlation=xcorr,
            seed=seed,
        )
    if kind == "autoencoder":
        from anomaly.models.autoencoder import AeResidual

        return AeResidual(
            hidden=config.get("ae_hidden", 64),
            bottleneck=config.get("ae_bottleneck", 8),
            epochs=config.get("ae_epochs", 200),
            quantile=quantile,
            cross_correlation=xcorr,
            seed=seed,
        )
    if kind == "iforest":
        from anomaly.models.iforest import IsolationForestDetector

        return IsolationForestDetector(
            n_estimators=config.get("iforest_trees", 200),
            quantile=quantile,
            cross_correlation=xcorr,
            seed=seed,
        )
    raise ValueError(f"미지원 detector: {kind}")


class _MergedPaths:
    """재구성(PCAX) + VAR 병합 검출기 (EXP-010, 서빙 통합과 동일 의미론)

    두 경로를 각자 한계로 정규화한 뒤 최대값을 점수로 사용, 서빙의 OR 발령과 동치
    """

    def __init__(self, n_components, quantile, cross_correlation, var_lag):
        from anomaly.models.var_residual import VarResidual

        self._pca = PcaMspc(n_components, quantile, cross_correlation)
        self._var = VarResidual(lag=var_lag, quantile=quantile)

    def fit(self, windows):
        self._pca.fit(windows)
        self._var.fit(windows)
        return self

    def score(self, windows):
        return np.maximum(self._pca.score(windows), self._var.score(windows))


def run_merged_paths(config: dict) -> dict[str, float]:
    """EXP-010 병합 경로 채점"""
    return _run_windowed(
        config,
        lambda: _MergedPaths(
            config["n_components"],
            config["threshold_quantile"],
            config.get("cross_correlation", True),
            config.get("var_lag", 2),
        ),
    )


def run_detector(config: dict) -> dict[str, float]:
    """EXP-009 비교군 채점, 파라미터·후처리는 PCA와 동일 고정"""
    return _run_windowed(config, lambda: _make_detector(config))


def run_ts2vec_knn(config: dict) -> dict[str, float]:
    """EXP-005 TS2Vec encoder + 거리 판정 채점 (baseline: knn | cosine)"""
    from anomaly.models.ts2vec import Ts2VecKnn  # torch 의존, 사용 시점 지연 import

    return _run_windowed(
        config,
        lambda: Ts2VecKnn(
            repr_dims=config["repr_dims"],
            depth=config["depth"],
            iters=config["iters"],
            batch_size=config["batch_size"],
            lr=config["lr"],
            k_neighbors=config["k_neighbors"],
            quantile=config["threshold_quantile"],
            baseline=config.get("baseline", "knn"),
            seed=config["seed"],
        ),
    )


def run_pca_mspc_ablation(config: dict) -> dict:
    """EXP-003 PCA MSPC 그리드 스윕, 지연-오탐 곡선용 전 조합 채점

    선정 기준: recall 1.0 조합 중 오탐/설비·일 최소, 동률이면 지연 p90 → mean 순
    반환: {"grid": 전 조합 결과, "best": 선정 조합, "best_metrics": 선정 조합 지표}
    """
    grid: list[dict] = []
    for window in config["windows"]:
        for stride in config["strides"]:
            for quantile in config["quantiles"]:
                for n_components in config["n_components_list"]:
                    params = {
                        "window": window,
                        "stride": stride,
                        "threshold_quantile": quantile,
                        "n_components": n_components,
                    }
                    metrics = run_pca_mspc({**config, **params})
                    grid.append({"params": params, "metrics": metrics})
                    print(
                        f"  w={window:3d} s={stride:2d} q={quantile} c={n_components}"
                        f" → FA/일 {metrics['false_alarms_per_equipment_day']:6.2f}"
                        f" recall {metrics['event_recall']:.2f}"
                        f" p90 {metrics['detection_delay_p90_ticks']:5.1f}"
                    )

    perfect = [g for g in grid if g["metrics"]["event_recall"] == 1.0]
    candidates = perfect or grid
    best = min(
        candidates,
        key=lambda g: (
            g["metrics"]["false_alarms_per_equipment_day"],
            g["metrics"]["detection_delay_p90_ticks"],
            g["metrics"]["detection_delay_mean_ticks"],
        ),
    )
    return {"grid": grid, "best": best["params"], "best_metrics": best["metrics"]}


def run_window_stride_sweep(config: dict) -> dict:
    """EXP-007 윈도우 x stride 스윕 (raw PCAX), 실시간성 지배 축 규명

    stride > window(데이터 skip) 조합은 제외, 유형별 지연 포함 전 조합 채점
    선정 기준: 급성 지연 최소 → 오탐 최소 (실시간 목표), ablation의 오탐 우선과 대비
    """
    grid: list[dict] = []
    for window in config["windows"]:
        for stride in config["strides"]:
            if stride > window:
                continue
            params = {"window": window, "stride": stride}
            metrics = run_pca_mspc({**config, **params})
            grid.append({"params": params, "metrics": metrics})
            print(
                f"  w={window * 5:3d}s s={stride * 5:3d}s → "
                f"급성 {metrics['delay_mean_acute_ticks'] * 5:5.0f}s "
                f"진동 {metrics.get('delay_mean_oscillation_ticks', float('nan')) * 5:6.0f}s "
                f"FA/일 {metrics['false_alarms_per_equipment_day']:6.1f}"
            )

    best = min(
        grid,
        key=lambda g: (
            g["metrics"]["delay_mean_acute_ticks"],
            g["metrics"]["false_alarms_per_equipment_day"],
        ),
    )
    return {"grid": grid, "best": best["params"], "best_metrics": best["metrics"]}


# method 키 → 채점 함수, 실험 추가 시 여기 등록
METHODS = {
    "rule_baseline": run_rule_baseline,
    "pca_mspc": run_pca_mspc,
    "pca_mspc_ablation": run_pca_mspc_ablation,
    "window_stride_sweep": run_window_stride_sweep,
    "ts2vec_knn": run_ts2vec_knn,
    "detector": run_detector,
    "merged_paths": run_merged_paths,
}


def main() -> None:
    import mlflow  # 지연 import, 채점 함수는 mlflow 없이 재사용 가능하게 유지(운영점 도구 등)

    parser = argparse.ArgumentParser(description="이상 감지 실험 러너")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text())
    name = config["name"]
    commit = _git_commit()
    eval_version = load_manifest(Path(config["eval_set"]))["version"]

    result = METHODS[config["method"]](config)
    is_grid = "grid" in result
    metrics = result["best_metrics"] if is_grid else result

    # MLflow 기록 (로컬 스토어), 그리드는 조합별 개별 run + 대표(best) run
    base_params = {
        "config_path": args.config,
        "method": config["method"],
        "eval_set": config["eval_set"],
        "eval_set_version": eval_version,
        "git_commit": commit,
    }
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    if is_grid:
        for entry in result["grid"]:
            suffix = "-".join(f"{k[0]}{v}" for k, v in entry["params"].items())
            with mlflow.start_run(run_name=f"{name}/{suffix}"):
                mlflow.log_params({**base_params, **entry["params"]})
                mlflow.log_metrics({k: v for k, v in entry["metrics"].items() if v == v})
    with mlflow.start_run(run_name=name):
        mlflow.log_params({**base_params, **(result["best"] if is_grid else {})})
        mlflow.log_metrics({k: v for k, v in metrics.items() if v == v})  # nan 제외

    # 확정 지표 스냅샷 (git 커밋 대상, 실험 문서 비교표의 단일 소스)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "experiment": name,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": commit,
        "eval_set_version": eval_version,
        "config": config,
        "metrics": metrics,
    }
    if is_grid:
        snapshot["best_params"] = result["best"]
        snapshot["grid"] = result["grid"]
    out_path = RESULTS_DIR / f"{name.lower().replace('-', '_')}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

    print(f"{name} 완료 → {out_path}")
    for key, value in metrics.items():
        print(f"  {key:35s}: {value:.4f}")


if __name__ == "__main__":
    main()
