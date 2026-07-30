"""운영점 비교 도구 (BE_ANOM01_EVAL01)

실행: uv run python experiments/operating_point.py

동일 오탐 예산에서 EXP-009 검출기를 임계 스윕으로 맞춰 비교
- 각 검출기 threshold_quantile 스윕 → dedup FAR 예산 이하 운영점(recall 최대·지연 최소) 선택
- 예산별 비교 표 출력 + FAR-recall 운영 곡선을 experiments/results/operating_point.json에 기록
  (곡선은 문서 그래프 재료, 운영점은 모델 간 동일 예산 비교의 단일 기준)
"""

import copy
import json
from pathlib import Path

import run  # 같은 experiments 디렉터리, run.py는 mlflow를 지연 import하므로 여기선 불필요
import yaml

from anomaly.evaluation import operating_point

DETECTORS = [
    ("PCA-both", "exp_009_pca_both"),
    ("PCA-SPE", "exp_009_pca_spe"),
    ("PCA-T2", "exp_009_pca_t2"),
    ("iForest", "exp_009_iforest"),
    ("kernelPCA", "exp_009_kpca"),
    ("VAR", "exp_009_var"),
    ("AE", "exp_009_ae"),
]
QUANTILES = [0.99, 0.995, 0.997, 0.999, 0.9995, 0.9999]
BUDGET = 1.0  # dedup FAR 예산 (건/설비·일)
CONFIG_DIR = Path("experiments/configs")
OUT = Path("experiments/results/operating_point.json")


def sweep_detector(stem: str) -> list[dict]:
    """검출기 config를 threshold_quantile만 바꿔 스윕, 스윕 점 metric 목록 반환"""
    base = yaml.safe_load((CONFIG_DIR / f"{stem}.yaml").read_text())
    points = []
    for q in QUANTILES:
        cfg = copy.deepcopy(base)
        cfg["threshold_quantile"] = q
        m = run.METHODS[cfg["method"]](cfg)
        points.append(
            {
                "threshold_quantile": q,
                "event_recall": m["event_recall"],
                "recall_drift_inband": m.get("recall_drift_inband"),
                "false_alarms_per_equipment_day": m["false_alarms_per_equipment_day"],
                "false_alarms_per_equipment_day_dedup": m["false_alarms_per_equipment_day_dedup"],
                "detection_delay_p90_ticks": m["detection_delay_p90_ticks"],
            }
        )
    return points


def main() -> None:
    result = {"budget_per_equipment_day": BUDGET, "quantiles": QUANTILES, "detectors": {}}
    rows = []
    for label, stem in DETECTORS:
        sweep = sweep_detector(stem)
        op = operating_point(sweep, BUDGET)
        result["detectors"][label] = {"operating_point": op, "curve": sweep}
        rows.append((label, op))

    print(f"\n동일 오탐 예산 {BUDGET}건/설비·일 운영점 비교")
    print(f"{'검출기':<10} {'q*':>7} {'recall':>7} {'di_recall':>9} {'dedupFA':>8} {'p90':>6}")
    for label, op in rows:
        print(
            f"{label:<10} {op['threshold_quantile']:>7.4f} {op['event_recall']:>7.3f} "
            f"{op['recall_drift_inband']:>9.3f} {op['false_alarms_per_equipment_day_dedup']:>8.2f} "
            f"{op['detection_delay_p90_ticks']:>6.0f}"
        )

    ranked = sorted(
        rows, key=lambda r: (-round(r[1]["event_recall"], 6), r[1]["detection_delay_p90_ticks"])
    )
    print("\n순위 (recall 내림차순 → 지연 오름차순)")
    for i, (label, op) in enumerate(ranked, 1):
        fa = op["false_alarms_per_equipment_day_dedup"]
        print(
            f"  {i}. {label}: recall {op['event_recall']:.3f}, "
            f"dedup FA {fa:.2f}, p90 {op['detection_delay_p90_ticks']:.0f}"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n곡선·운영점 기록 → {OUT}")


if __name__ == "__main__":
    main()
