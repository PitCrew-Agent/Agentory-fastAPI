"""anomaly.evaluation 단위 테스트"""

import math

import numpy as np

from anomaly.evaluation import (
    AnomalyEvent,
    auc_pr,
    bucket_dedup,
    event_recall_and_delays,
    false_alarms,
    point_labels,
    summarize,
)

EVENTS = [
    AnomalyEvent("EQP-1", "acute", ("temperature",), start_tick=10, end_tick=20),
    AnomalyEvent("EQP-2", "drift", ("pressure",), start_tick=5, end_tick=15),
]


def test_event_recall_and_delays():
    detections = {
        "EQP-1": np.array([12, 14]),  # 구간 내 첫 판정 12 → 지연 2
        "EQP-2": np.array([2, 30]),  # 구간 [5, 15] 밖 → 미감지
    }
    recall, delays, missed = event_recall_and_delays(EVENTS, detections)
    assert recall == 0.5
    assert delays == [2]
    assert missed == [EVENTS[1]]


def test_event_recall_respects_max_delay_grace():
    # EQP-1 이벤트 [10, 20], 판정 15 → 유예 3이면 미인정(마감 13), 유예 5면 인정
    detections = {"EQP-1": np.array([15]), "EQP-2": np.array([])}
    recall_tight, delays, missed = event_recall_and_delays(EVENTS, detections, max_delay_ticks=3)
    assert recall_tight == 0.0 and delays == [] and len(missed) == 2
    recall_loose, delays, _missed = event_recall_and_delays(EVENTS, detections, max_delay_ticks=5)
    assert recall_loose == 0.5 and delays == [5]


def test_event_recall_empty_events_is_nan():
    recall, delays, missed = event_recall_and_delays([], {})
    assert math.isnan(recall)
    assert delays == [] and missed == []


def test_false_alarms_counts_outside_events_only():
    detections = {
        "EQP-1": np.array([1, 12, 25]),  # 12는 이벤트 내, 1·25는 오탐
        "EQP-2": np.array([10]),  # 이벤트 내
    }
    # 설비당 8640 tick x 10초 = 1일씩, 총 2설비·일
    n_ticks = {"EQP-1": 8640, "EQP-2": 8640}
    total, per_day = false_alarms(EVENTS, detections, n_ticks, tick_seconds=10.0)
    assert total == 2
    assert per_day == 1.0


def test_bucket_dedup_keeps_first_per_fixed_bucket():
    ticks = np.array([0, 6, 12, 360, 366, 900])
    # bucket_ticks=360 → 버킷 0(0·6·12), 1(360·366), 2(900), 각 최초만
    assert bucket_dedup(ticks, 360).tolist() == [0, 360, 900]


def test_bucket_dedup_passthrough_when_bucket_leq_one():
    ticks = np.array([1, 2, 3])
    assert bucket_dedup(ticks, 1).tolist() == [1, 2, 3]


def test_false_alarms_dedup_collapses_burst_within_bucket():
    # 이벤트 밖 한 30분버킷(360~719 tick) 안 10건 연속 발령
    burst = np.arange(400, 460, 6)
    detections = {"EQP-1": burst}
    n_ticks = {"EQP-1": 8640}
    raw_total, _ = false_alarms(EVENTS, detections, n_ticks, tick_seconds=5.0)
    dedup_total, _ = false_alarms(
        EVENTS, detections, n_ticks, tick_seconds=5.0, dedup_bucket_seconds=1800.0
    )
    assert raw_total == 10
    assert dedup_total == 1


def test_summarize_reports_dedup_false_alarms():
    burst = np.arange(400, 460, 6)
    detections = {"EQP-1": burst, "EQP-2": np.array([], dtype=int)}
    n_ticks = {"EQP-1": 8640, "EQP-2": 8640}
    metrics = summarize(EVENTS, detections, n_ticks, tick_seconds=5.0)
    assert metrics["false_alarms_total"] == 10.0
    assert metrics["false_alarms_dedup_total"] == 1.0
    assert (
        metrics["false_alarms_per_equipment_day_dedup"] < metrics["false_alarms_per_equipment_day"]
    )


def test_point_labels_marks_event_range_inclusive():
    labels = point_labels("EQP-1", n_ticks=30, events=EVENTS)
    assert labels[9] == 0 and labels[10] == 1 and labels[20] == 1 and labels[21] == 0
    # 다른 설비 이벤트는 미반영
    assert labels.sum() == 11


def test_auc_pr_single_class_is_nan():
    assert math.isnan(auc_pr(np.array([0.1, 0.2]), np.array([0, 0])))


def test_auc_pr_perfect_ranking():
    scores = np.array([0.1, 0.2, 0.8, 0.9])
    labels = np.array([0, 0, 1, 1])
    assert auc_pr(scores, labels) == 1.0


def test_summarize_keys_and_values():
    detections = {"EQP-1": np.array([12]), "EQP-2": np.array([7])}
    n_ticks = {"EQP-1": 100, "EQP-2": 100}
    metrics = summarize(EVENTS, detections, n_ticks, tick_seconds=5.0)
    assert metrics["event_recall"] == 1.0
    assert metrics["events_detected"] == 2.0
    assert metrics["false_alarms_total"] == 0.0
    assert metrics["detection_delay_mean_ticks"] == 2.0


def test_summarize_per_kind_recall_breakdown():
    # acute만 감지, drift 미감지 → 유형별 recall이 집계를 분해
    detections = {"EQP-1": np.array([12]), "EQP-2": np.array([], dtype=int)}
    n_ticks = {"EQP-1": 100, "EQP-2": 100}
    metrics = summarize(EVENTS, detections, n_ticks, tick_seconds=5.0)
    assert metrics["event_recall"] == 0.5
    assert metrics["recall_acute"] == 1.0
    assert metrics["recall_drift"] == 0.0
