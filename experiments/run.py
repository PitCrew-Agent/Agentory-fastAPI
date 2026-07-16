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
import yaml

from anomaly.eval_set import load_eval_frame, load_events, load_manifest
from anomaly.evaluation import auc_pr, summarize
from anomaly.rule_baseline import equipment_n_ticks, rule_detections, rule_point_scores

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


# method 키 → 채점 함수, 실험 추가 시 여기 등록
METHODS = {
    "rule_baseline": run_rule_baseline,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="이상 감지 실험 러너")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text())
    name = config["name"]
    commit = _git_commit()
    eval_version = load_manifest(Path(config["eval_set"]))["version"]

    metrics = METHODS[config["method"]](config)

    # MLflow 기록 (로컬 파일 스토어)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name=name):
        mlflow.log_params(
            {
                "config_path": args.config,
                "method": config["method"],
                "eval_set": config["eval_set"],
                "eval_set_version": eval_version,
                "git_commit": commit,
            }
        )
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
    out_path = RESULTS_DIR / f"{name.lower().replace('-', '_')}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

    print(f"{name} 완료 → {out_path}")
    for key, value in metrics.items():
        print(f"  {key:35s}: {value:.4f}")


if __name__ == "__main__":
    main()
