"""이상 감지 실험 공통 평가기

전 실험(E1 규칙 베이스라인, E2 PCA MSPC, E4 TS2Vec)이 동일 지표로 채점되도록 단일 소스 유지
지표 4종 + 오탐 dedup 변형
- 이벤트 단위 recall: 정답 이벤트 구간 내 판정 1건 이상이면 감지
- 설비·일당 오탐 수: 이벤트 밖 판정 수를 (설비 수 x 관측일)로 정규화, 운영자 신뢰 지표
- 감지 지연: 이벤트 시작 tick부터 첫 판정 tick까지 (tick 단위)
- AUC-PR: 포인트 단위 점수 순위 품질, 임계 무관 모델 간 비교용
- 오탐 dedup 변형: 운영 알림화(설비+변수+알람+30분 고정버킷 1건)를 근사한 버킷당 1건 집계,
  지속·flicker 발령이 버킷당 다건으로 과대 집계되는 것을 보정한 운영 정합 오탐률
"""

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score

SECONDS_PER_DAY = 86400
# 운영 알림 dedup 고정버킷 폭, notification.sync_from_alarms의 interval '30 minutes'와 정합
DEDUP_BUCKET_SECONDS = 1800.0


@dataclass(frozen=True)
class AnomalyEvent:
    """평가 세트 정답 이벤트 1건, (설비 x 이상 유형 x 대상 변수 집합) 단위"""

    equipment_id: str
    kind: str  # acute | drift | variance
    variables: tuple[str, ...]
    start_tick: int
    end_tick: int


def resolve_grace(kind: str, max_delay_ticks: int | dict | None) -> int | None:
    """이상 유형별 감지 유예 해석

    max_delay_ticks가 int·None이면 전 유형 공통, dict이면 유형별 유예 매핑으로 해석
    (매핑에 없는 유형은 "_default" 키로 폴백, 그마저 없으면 유예 무제한)
    느린 유형(예: drift_inband)은 물리적으로 짧은 유예 내 감지 불가하므로 유형별 지정 필요
    """
    if isinstance(max_delay_ticks, dict):
        return max_delay_ticks.get(kind, max_delay_ticks.get("_default"))
    return max_delay_ticks


def event_recall_and_delays(
    events: list[AnomalyEvent],
    detections: dict[str, np.ndarray],
    max_delay_ticks: int | dict | None = None,
) -> tuple[float, list[int], list[AnomalyEvent]]:
    """이벤트별 감지 여부·지연 산출

    detections는 설비별 이상 판정 tick 오름차순 배열
    max_delay_ticks는 감지 유예, 이벤트 시작 후 이 안에 판정해야 감지로 인정
    (미지정 시 이벤트 전 구간 인정, 장구간 이벤트는 배경 오탐의 우연 적중으로
    recall이 포화하므로 v2부터 유예 지정을 권장)
    dict 지정 시 유형별 유예 적용 (resolve_grace 참고, 유형별 감지 물리 한계 반영)
    반환: (recall, 감지 이벤트별 지연 tick 목록, 미감지 이벤트 목록)
    """
    delays: list[int] = []
    missed: list[AnomalyEvent] = []
    for event in events:
        ticks = detections.get(event.equipment_id, np.empty(0, dtype=int))
        grace = resolve_grace(event.kind, max_delay_ticks)
        deadline = event.end_tick
        if grace is not None:
            deadline = min(deadline, event.start_tick + grace)
        in_window = ticks[(ticks >= event.start_tick) & (ticks <= deadline)]
        if in_window.size:
            delays.append(int(in_window[0]) - event.start_tick)
        else:
            missed.append(event)
    recall = (len(events) - len(missed)) / len(events) if events else float("nan")
    return recall, delays, missed


def bucket_dedup(ticks: np.ndarray, bucket_ticks: int) -> np.ndarray:
    """고정버킷(원점 tick 0 정렬)당 최초 판정 1건만 남김

    운영 알림화(설비+변수+알람+30분 고정버킷 1건, notification.sync_from_alarms의 date_bin)를
    실험 detection에 근사, 지속·flicker 발령이 버킷당 다건으로 과대 집계되는 것 방지
    실험 detection에는 변수·알람코드 축이 없어 설비+버킷 단위로만 축약(운영 대비 보수적 근사)
    bucket_ticks<=1 또는 빈 입력이면 원본 그대로
    """
    if bucket_ticks <= 1 or ticks.size == 0:
        return ticks
    buckets = ticks // bucket_ticks
    _, first_idx = np.unique(buckets, return_index=True)
    return ticks[np.sort(first_idx)]


def false_alarms(
    events: list[AnomalyEvent],
    detections: dict[str, np.ndarray],
    n_ticks: dict[str, int],
    tick_seconds: float,
    dedup_bucket_seconds: float | None = None,
) -> tuple[int, float]:
    """정답 이벤트 밖 판정을 오탐으로 집계

    n_ticks는 설비별 총 관측 tick 수
    dedup_bucket_seconds 지정 시 운영 알림 dedup을 근사해 설비·고정버킷당 오탐 1건만 집계
    반환: (총 오탐 수, 설비·일당 오탐 수)
    """
    bucket_ticks = (
        max(1, round(dedup_bucket_seconds / tick_seconds)) if dedup_bucket_seconds else None
    )
    total = 0
    total_days = 0.0
    for equipment_id, ticks in detections.items():
        outside = np.ones(ticks.shape, dtype=bool)
        for event in events:
            if event.equipment_id == equipment_id:
                outside &= ~((ticks >= event.start_tick) & (ticks <= event.end_tick))
        outside_ticks = ticks[outside]
        if bucket_ticks is not None:
            outside_ticks = bucket_dedup(outside_ticks, bucket_ticks)
        total += int(outside_ticks.size)
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
    max_delay_ticks: int | dict | None = None,
    dedup_bucket_seconds: float | None = DEDUP_BUCKET_SECONDS,
) -> dict[str, float]:
    """전 지표 일괄 산출, MLflow log_metrics에 그대로 기록 가능한 평탄 dict 반환

    recall·지연은 max_delay_ticks 유예 적용, 오탐은 이벤트 전 구간을 비오탐 구역으로 유지
    (유예 이후의 구간 내 판정은 미인정일 뿐 오탐은 아님)
    dedup_bucket_seconds 지정 시 운영 정합 오탐(버킷당 1건)을 raw 오탐과 함께 기록
    """
    recall, delays, missed = event_recall_and_delays(events, detections, max_delay_ticks)
    fa_total, fa_per_day = false_alarms(events, detections, n_ticks, tick_seconds)
    metrics = {
        "events_total": float(len(events)),
        "events_detected": float(len(events) - len(missed)),
        "event_recall": recall,
        "false_alarms_total": float(fa_total),
        "false_alarms_per_equipment_day": fa_per_day,
        "detection_delay_mean_ticks": float(np.mean(delays)) if delays else float("nan"),
        "detection_delay_p90_ticks": float(np.percentile(delays, 90)) if delays else float("nan"),
    }
    # 운영 알림 dedup 근사 오탐, raw 대비 지속·flicker 과대집계 제거분을 ADR 정량 비교에 사용
    if dedup_bucket_seconds:
        fa_dedup_total, fa_dedup_per_day = false_alarms(
            events, detections, n_ticks, tick_seconds, dedup_bucket_seconds
        )
        metrics["false_alarms_dedup_total"] = float(fa_dedup_total)
        metrics["false_alarms_per_equipment_day_dedup"] = fa_dedup_per_day
    # 유형별 recall·지연 분해, 집계값이 이상 유형 구성에 가려지는 것 방지 (EXP-004·007 교훈)
    for kind in sorted({e.kind for e in events}):
        subset = [e for e in events if e.kind == kind]
        kind_recall, kind_delays, _missed = event_recall_and_delays(
            subset, detections, max_delay_ticks
        )
        metrics[f"recall_{kind}"] = kind_recall
        metrics[f"delay_mean_{kind}_ticks"] = (
            float(np.mean(kind_delays)) if kind_delays else float("nan")
        )
    return metrics


def operating_point(
    sweep: list[dict],
    budget: float,
    fa_key: str = "false_alarms_per_equipment_day_dedup",
    recall_key: str = "event_recall",
    delay_key: str = "detection_delay_p90_ticks",
) -> dict | None:
    """오탐 예산 이하 스윕 점 중 운영점 선택 (recall 최대, 동률이면 지연 최소)

    sweep은 임계 스윕 결과 metric dict 목록, budget은 fa_key 상한(건/설비·일)
    예산 이하 점이 없으면 오탐 최저 점 반환, 모델 간 동일 예산 비교의 단일 기준
    빈 sweep이면 None
    """
    if not sweep:
        return None
    eligible = [p for p in sweep if p.get(fa_key, float("inf")) <= budget]
    pool = eligible or [min(sweep, key=lambda p: p.get(fa_key, float("inf")))]
    return max(
        pool,
        key=lambda p: (round(p.get(recall_key, 0.0), 6), -p.get(delay_key, float("inf"))),
    )
