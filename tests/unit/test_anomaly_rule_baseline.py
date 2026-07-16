"""anomaly.rule_baseline 단위 테스트"""

import numpy as np
import pandas as pd

from anomaly.evaluation import AnomalyEvent
from anomaly.rule_baseline import equipment_n_ticks, rule_detections, rule_point_scores

FRAME = pd.DataFrame(
    {
        "equipment_id": ["EQP-1"] * 4 + ["EQP-2"] * 3,
        "tick": [0, 1, 2, 3, 0, 1, 2],
        "alarm_code": [None, "ERR-401", None, "WRN-701", None, None, None],
    }
)


def test_rule_detections_extracts_alarm_ticks_sorted():
    detections = rule_detections(FRAME)
    assert detections["EQP-1"].tolist() == [1, 3]
    assert detections["EQP-2"].tolist() == []


def test_equipment_n_ticks():
    assert equipment_n_ticks(FRAME) == {"EQP-1": 4, "EQP-2": 3}


def test_rule_point_scores_binary_and_aligned():
    events = [AnomalyEvent("EQP-1", "acute", ("temperature",), start_tick=1, end_tick=3)]
    scores, labels = rule_point_scores(FRAME, events)
    assert scores.tolist() == [0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    assert labels.tolist() == [0, 1, 1, 1, 0, 0, 0]
    assert scores.shape == labels.shape


def test_rule_point_scores_no_events_all_zero_labels():
    scores, labels = rule_point_scores(FRAME, [])
    assert labels.sum() == 0
    assert np.count_nonzero(scores) == 2
