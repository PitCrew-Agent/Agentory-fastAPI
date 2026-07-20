"""UMAP 기반 정상 운전 구조 탐색 (오프라인 분석 전용, 서빙 무관)

실행: uv run python experiments/umap_explore.py [--out docs/experiments/assets]
목적 세 가지
- Q1 정상 운전이 몇 개 모드로 갈라지는가 (다중 모드 여부)
- Q2 설비별로 군집이 갈리는가 (설비별 캘리브 실효성 근거)
- Q3 라벨된 이상이 정상 다양체 어디에 떨어지는가 (평가셋 v2)

UMAP은 군집 구조 탐색용으로만 사용하며 검출기로 쓰지 않음 (거리 정량 해석 불가,
재구성·채널 귀속 부재, 비결정성 → 서빙 아키텍처 부적합)
정량 판단은 실루엣 점수로 하고 그림은 보조
"""

import argparse
import asyncio
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import umap
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from agentory.core.db import SessionLocal
from agentory.modules.watcher.fit import _equipment_by_type, _normal_windows
from anomaly.eval_set import load_eval_frame, load_manifest
from anomaly.features import window_features
from anomaly.windowing import sliding_windows
from simulator.generator import VARS

WINDOW, STRIDE = 12, 6
SAMPLE_PER_EQUIPMENT = 400  # 설비당 표본, UMAP 계산량 억제
SEED = 20260720


def _embed(features: np.ndarray, seed: int = SEED) -> np.ndarray:
    scaled = StandardScaler().fit_transform(features)
    reducer = umap.UMAP(n_neighbors=30, min_dist=0.1, random_state=seed)
    return reducer.fit_transform(scaled)


def _subsample(rng: np.random.Generator, arr: np.ndarray, n: int) -> np.ndarray:
    if arr.shape[0] <= n:
        return arr
    return arr[rng.choice(arr.shape[0], n, replace=False)]


async def _collect_live_normal() -> tuple[np.ndarray, np.ndarray]:
    """dev DB 정상 telemetry를 설비별로 표본 추출, (피처, 설비라벨) 반환"""
    rng = np.random.default_rng(SEED)
    feats, labels = [], []
    async with SessionLocal() as session:
        by_type = await _equipment_by_type(session)
        for _ptype, equipment in by_type.items():
            for eid, repaired_at in equipment:
                w = await _normal_windows(session, eid, WINDOW, STRIDE, repaired_at)
                if w.shape[0] < 50:
                    continue
                sampled = _subsample(rng, w, SAMPLE_PER_EQUIPMENT)
                feats.append(window_features(sampled, cross_correlation=True))
                labels.extend([eid] * sampled.shape[0])
    return np.concatenate(feats), np.array(labels)


IGNITION_RAMP_TICKS = 120  # 램프 구간만 ignition으로 라벨, 이후는 정상이라 제외


def _collect_eval_v2() -> tuple[np.ndarray, np.ndarray]:
    """평가셋 v2 합성 세그먼트를 시나리오 라벨과 함께 피처화

    각 라벨이 실제 해당 상태인 구간만 사용 (ignition은 램프, 이상은 주입 이후)
    전 구간을 쓰면 대부분이 정상이라 라벨이 오염됨
    """
    root = Path("data/eval/v2")
    frame = load_eval_frame(root)
    cfg = load_manifest(root)["config"]
    scenario = {s["equipment_id"]: s["scenario"] for s in cfg["eval"]["segments"]}
    onset = cfg["eval"]["drift_start_tick"]
    rng = np.random.default_rng(SEED)
    feats, labels = [], []
    for eid, group in frame.groupby("equipment_id"):
        if not str(eid).startswith("SYN"):
            continue  # 결합 채널 합성 세그먼트만 (동일 공정 유형 유지)
        scn = scenario[str(eid)]
        ordered = group.sort_values("tick")
        matrix = ordered[list(VARS)].to_numpy()
        if scn == "normal":
            segment = matrix
        elif scn == "ignition":
            segment = matrix[:IGNITION_RAMP_TICKS]  # 램프만
        else:
            segment = matrix[onset:]  # 이상 주입 이후
        w = sliding_windows(segment, WINDOW, STRIDE)
        if w.shape[0] == 0:
            continue
        sampled = _subsample(rng, w, SAMPLE_PER_EQUIPMENT)
        feats.append(window_features(sampled, cross_correlation=True))
        labels.extend([scn] * sampled.shape[0])
    return np.concatenate(feats), np.array(labels)


def _scatter(embedding: np.ndarray, labels: np.ndarray, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 7))
    for label in sorted(set(labels)):
        mask = labels == label
        ax.scatter(embedding[mask, 0], embedding[mask, 1], s=4, alpha=0.5, label=str(label))
    ax.set_title(title)
    ax.legend(markerscale=3, fontsize=7, ncol=2, loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _silhouette(data: np.ndarray, labels: np.ndarray, name: str) -> float:
    if len(set(labels)) < 2:
        return float("nan")
    score = float(silhouette_score(data, labels))
    print(f"  실루엣({name}): {score:+.3f}")
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description="UMAP 정상 구조 탐색")
    parser.add_argument("--out", default="docs/experiments/assets")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("=== Q1·Q2 실 telemetry 정상 구조 (dev DB) ===")
    feats, equipment = asyncio.run(_collect_live_normal())
    print(f"  표본 {feats.shape[0]}개, 설비 {len(set(equipment))}대, 피처 {feats.shape[1]}차원")
    embedding = _embed(feats)
    # 설비 분리도, 원 피처공간과 UMAP 공간 모두 측정 (UMAP이 구조를 만들어낸 건 아닌지 대조)
    raw_sil = _silhouette(StandardScaler().fit_transform(feats), equipment, "원 피처공간·설비")
    umap_sil = _silhouette(embedding, equipment, "UMAP공간·설비")
    _scatter(
        embedding,
        equipment,
        "live normal telemetry UMAP (by equipment)",
        out / "umap_live_equipment.png",
    )

    print("=== Q3 평가셋 v2 이상 유형별 구조 ===")
    ev_feats, ev_labels = _collect_eval_v2()
    counts = {s: int((ev_labels == s).sum()) for s in sorted(set(ev_labels))}
    print(f"  표본 {ev_feats.shape[0]}개, 유형별 {counts}")
    ev_embedding = _embed(ev_feats)
    _scatter(ev_embedding, ev_labels, "eval v2 UMAP by scenario", out / "umap_eval_scenario.png")

    # 유형별 vs 정상 이분 분리도, 통합 실루엣보다 해석이 명확
    print("  이상 유형별 정상 대비 분리도 (원공간 / UMAP공간)")
    scaled_ev = StandardScaler().fit_transform(ev_feats)
    normal_mask = ev_labels == "normal"
    pairwise: dict[str, tuple[float, float]] = {}
    for scn in sorted(set(ev_labels) - {"normal"}):
        mask = normal_mask | (ev_labels == scn)
        binary = np.where(ev_labels[mask] == "normal", "normal", scn)
        raw = float(silhouette_score(scaled_ev[mask], binary))
        emb = float(silhouette_score(ev_embedding[mask], binary))
        pairwise[scn] = (raw, emb)
        print(f"    {scn:16} {raw:+.3f} / {emb:+.3f}")

    print("=== 요약 ===")
    print(f"  설비 분리도: 원공간 {raw_sil:+.3f} / UMAP {umap_sil:+.3f}")
    print(f"  그림 저장: {out}")


if __name__ == "__main__":
    main()
