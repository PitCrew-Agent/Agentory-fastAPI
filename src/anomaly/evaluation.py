"""이상 감지 실험 공통 평가기

전 실험(E1 규칙 베이스라인, E2 PCA MSPC, E4 TS2Vec)이 동일 지표로 채점되도록 단일 소스 유지
지표 4종
- 이벤트 단위 recall: 정답 이벤트 구간 내 판정 1건 이상이면 감지
- 설비·일당 오탐 수: 이벤트 밖 판정 수를 (설비 수 x 관측일)로 정규화, 운영자 신뢰 지표
- 감지 지연: 이벤트 시작 tick부터 첫 판정 tick까지 (tick 단위)
- AUC-PR: 포인트 단위 점수 순위 품질, 임계 무관 모델 간 비교용
"""

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class AnomalyEvent:
    """평가 세트 정답 이벤트 1건, (설비 x 이상 유형 x 대상 변수 집합) 단위"""

    equipment_id: str
    kind: str  # acute | drift | variance
    variables: tuple[str, ...]
    start_tick: int
    end_tick: int


def event_recall_and_delays(
    events: list[AnomalyEvent], detections: dict[str, np.ndarray]
) -> tuple[float, list[int], list[AnomalyEvent]]:
    """이벤트별 감지 여부·지연 산출

    detections는 설비별 이상 판정 tick 오름차순 배열
    반환: (recall, 감지 이벤트별 지연 tick 목록, 미감지 이벤트 목록)
    """
    delays: list[int] = []
    missed: list[AnomalyEvent] = []
    for event in events:
        ticks = detections.get(event.equipment_id, np.empty(0, dtype=int))
        in_window = ticks[(ticks >= event.start_tick) & (ticks <= event.end_tick)]
        if in_window.size:
            delays.append(int(in_window[0]) - event.start_tick)
        else:
            missed.append(event)
    recall = (len(events) - len(missed)) / len(events) if events else float("nan")
    return recall, delays, missed


def false_alarms(
    events: list[AnomalyEvent],
    detections: dict[str, np.ndarray],
    n_ticks: dict[str, int],
    tick_seconds: float,
) -> tuple[int, float]:
    """정답 이벤트 밖 판정을 오탐으로 집계

    n_ticks는 설비별 총 관측 tick 수
    반환: (총 오탐 수, 설비·일당 오탐 수)
    """
    total = 0
    total_days = 0.0
    for equipment_id, ticks in detections.items():
        outside = np.ones(ticks.shape, dtype=bool)
        for event in events:
            if event.equipment_id == equipment_id:
                outside &= ~((ticks >= event.start_tick) & (ticks <= event.end_tick))
        total += int(outside.sum())
    for count in n_ticks.values():
        total_days += count * tick_seconds / SECONDS_PER_DAY
    per_day = total / total_days if total_days else float("nan")
    return total, per_day


def point_labels(equipment_id: str, n_ticks: int, events: list[AnomalyEvent]) -> np.ndarray:
    """tick 단위 정답 라벨 (이벤트 구간 내 1), AUC-PR 계산용"""
    labels = np.zeros(n_ticks, dtype=int)
    for event in events:
        if event.equipment_id == equipment_id:
            labels[event.start_tick : event.end_tick + 1] = 1
    return labels


def auc_pr(scores: np.ndarray, labels: np.ndarray) -> float:
    """포인트 단위 AUC-PR, 라벨이 단일 클래스면 nan"""
    if np.unique(labels).size < 2:
        return float("nan")
    return float(average_precision_score(labels, scores))


def summarize(
    events: list[AnomalyEvent],
    detections: dict[str, np.ndarray],
    n_ticks: dict[str, int],
    tick_seconds: float,
) -> dict[str, float]:
    """전 지표 일괄 산출, MLflow log_metrics에 그대로 기록 가능한 평탄 dict 반환"""
    recall, delays, missed = event_recall_and_delays(events, detections)
    fa_total, fa_per_day = false_alarms(events, detections, n_ticks, tick_seconds)
    return {
        "events_total": float(len(events)),
        "events_detected": float(len(events) - len(missed)),
        "event_recall": recall,
        "false_alarms_total": float(fa_total),
        "false_alarms_per_equipment_day": fa_per_day,
        "detection_delay_mean_ticks": float(np.mean(delays)) if delays else float("nan"),
        "detection_delay_p90_ticks": float(np.percentile(delays, 90)) if delays else float("nan"),
    }
