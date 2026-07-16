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

import mlflow
import numpy as np
import yaml

from anomaly.eval_set import load_eval_frame, load_events, load_manifest, load_train_frame
from anomaly.evaluation import auc_pr, point_labels, summarize
from anomaly.models.pca_mspc import PcaMspc
from anomaly.rule_baseline import equipment_n_ticks, rule_detections, rule_point_scores
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


def run_rule_baseline(config: dict) -> dict[str, float]:
    """EXP-001 규칙 레이어 단독 채점, 평가 세트 저장 컬럼만 사용 (재실행 없음)"""
    root = Path(config["eval_set"])
    frame = load_eval_frame(root)
    events = load_events(root)
    tick_seconds = load_manifest(root)["config"]["tick_seconds"]

    metrics = summarize(events, rule_detections(frame), equipment_n_ticks(frame), tick_seconds)
    scores, labels = rule_point_scores(frame, events)
    metrics["auc_pr"] = auc_pr(scores, labels)
    return metrics


def run_pca_mspc(config: dict) -> dict[str, float]:
    """EXP-002 PCA MSPC (T²+SPE) 채점

    공정 유형별 모델 적합 (설비별 캘리브레이션은 EXP-003 이후), 판정 tick은 윈도우 끝
    포인트 점수는 각 tick 시점까지 완료된 최신 윈도우 점수를 할당 (첫 윈도우 이전은 첫 점수)
    """
    root = Path(config["eval_set"])
    train = load_train_frame(root)
    frame = load_eval_frame(root)
    events = load_events(root)
    manifest_cfg = load_manifest(root)["config"]
    tick_seconds = manifest_cfg["tick_seconds"]
    window, stride = config["window"], config["stride"]

    # 설비 → 공정 유형 매핑은 manifest의 config 스냅샷에서 복원 (평가 세트 재생성 불필요)
    eval_types = {s["equipment_id"]: s["process_type"] for s in manifest_cfg["eval"]["segments"]}
    train_types = {
        e["equipment_id"]: e["process_type"] for e in manifest_cfg["train"]["equipments"]
    }

    models: dict[str, PcaMspc] = {}
    for ptype in sorted(set(train_types.values())):
        parts = [
            sliding_windows(
                train[train["equipment_id"] == eq].sort_values("tick")[list(VARS)].to_numpy(),
                window,
                stride,
            )
            for eq, t in train_types.items()
            if t == ptype
        ]
        model = PcaMspc(config["n_components"], config["threshold_quantile"])
        models[ptype] = model.fit(np.concatenate(parts))

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
        scores = models[eval_types[str(equipment_id)]].score(windows)
        detections[str(equipment_id)] = ends[scores > 1.0]
        latest = np.clip(np.searchsorted(ends, np.arange(n), side="right") - 1, 0, None)
        scores_all.append(scores[latest])
        labels_all.append(point_labels(str(equipment_id), n, events))

    metrics = summarize(events, detections, n_ticks, tick_seconds)
    metrics["auc_pr"] = auc_pr(np.concatenate(scores_all), np.concatenate(labels_all))
    return metrics


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


# method 키 → 채점 함수, 실험 추가 시 여기 등록
METHODS = {
    "rule_baseline": run_rule_baseline,
    "pca_mspc": run_pca_mspc,
    "pca_mspc_ablation": run_pca_mspc_ablation,
}


def main() -> None:
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
