"""이상 감지 평가 요약 그림 생성 (BE_ANOM01_EVAL01)

실행: uv run --with matplotlib python experiments/plot_eval_summary.py

docs/experiments/anomaly-model-summary.md의 그림 3종을 docs/experiments/assets/에 생성
- operating_curve.png: 검출기별 recall vs dedup FAR 운영 곡선(예산선 표시)
- dedup_inflation.png: raw vs dedup FAR 과대집계(검출기별)
- drift_snr.png: drift_inband SNR vs 적분 구간(유예 180·720 표시)
라벨은 matplotlib 한글 폰트 의존 회피 위해 영어로 작성
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ART = Path("experiments/results/operating_point.json")
ASSETS = Path("docs/experiments/assets")
BUDGET = 1.0
Q_DEFAULT = 0.999  # config 기본 임계, dedup 뻥튀기 비교 기준점


def load_curves() -> dict:
    return json.loads(ART.read_text())["detectors"]


def plot_operating_curve(detectors: dict) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for name, v in detectors.items():
        curve = sorted(v["curve"], key=lambda p: p["false_alarms_per_equipment_day_dedup"])
        xs = [p["false_alarms_per_equipment_day_dedup"] for p in curve]
        ys = [p["event_recall"] for p in curve]
        ax.plot(xs, ys, marker="o", markersize=3, label=name, alpha=0.85)
    ax.axvline(BUDGET, color="crimson", ls="--", lw=1, label=f"budget {BUDGET}/equip-day")
    ax.set_xlabel("dedup false alarms per equipment-day")
    ax.set_ylabel("event recall")
    ax.set_title("Operating curves: recall vs false-alarm rate (v2)")
    ax.set_ylim(0.80, 1.02)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(ASSETS / "operating_curve.png", dpi=130)
    plt.close(fig)


def plot_dedup_inflation(detectors: dict) -> None:
    names, raw, dedup = [], [], []
    for name, v in detectors.items():
        pt = next(p for p in v["curve"] if abs(p["threshold_quantile"] - Q_DEFAULT) < 1e-9)
        names.append(name)
        raw.append(pt["false_alarms_per_equipment_day"])
        dedup.append(pt["false_alarms_per_equipment_day_dedup"])
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.bar(x - 0.2, raw, 0.4, label="raw FAR (per detection tick)", color="#c0392b")
    ax.bar(x + 0.2, dedup, 0.4, label="dedup FAR (operation-aligned)", color="#2980b9")
    for i, (r, d) in enumerate(zip(raw, dedup, strict=True)):
        ax.text(i, max(r, d) + 0.2, f"{r / d:.1f}x", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, fontsize=8)
    ax.set_ylabel("false alarms per equipment-day")
    ax.set_title("Raw vs operation-aligned false-alarm rate (q0.999)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(ASSETS / "dedup_inflation.png", dpi=130)
    plt.close(fig)


def plot_drift_snr() -> None:
    df = pd.read_parquet("data/eval/v2/eval.parquet")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    horizons = np.arange(30, 2001, 10)
    for eq in ["SYN-L01", "SYN-L02"]:
        s = df[df.equipment_id == eq].sort_values("tick")
        pre = s[s.tick < 720]["temperature"].to_numpy()
        ev = s[(s.tick >= 720) & (s.tick <= 4319)]["temperature"].to_numpy()
        local_sd = pre.std()
        slope = np.polyfit(np.arange(ev.size), ev, 1)[0]
        snr = np.abs(slope) * horizons / (local_sd / np.sqrt(horizons))
        ax.plot(horizons, snr, label=f"{eq} (local σ={local_sd:.3f})")
    ax.axvline(180, color="crimson", ls="--", lw=1, label="grace 180 tick (15 min)")
    ax.axvline(720, color="green", ls="--", lw=1, label="grace 720 tick (60 min)")
    ax.axhline(2.5, color="gray", ls=":", lw=1, label="SNR 2.5 (detectable)")
    ax.set_xlabel("integration horizon (tick, 5 s each)")
    ax.set_ylabel("drift signal-to-noise ratio")
    ax.set_title("drift_inband detectability vs integration horizon")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(ASSETS / "drift_snr.png", dpi=130)
    plt.close(fig)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    detectors = load_curves()
    plot_operating_curve(detectors)
    plot_dedup_inflation(detectors)
    plot_drift_snr()
    print(f"그림 3종 생성 완료 → {ASSETS}")


if __name__ == "__main__":
    main()
