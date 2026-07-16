"""EXP-001 규칙 레이어 베이스라인 채점 어댑터

평가 세트에 저장된 규칙 판정 컬럼(alarm_code)을 공용 평가기 입력 형식으로 변환
시뮬레이터 재실행 없이 저장 컬럼만으로 현행 임계·알람 체계의 지표 산출
"""

import numpy as np
import pandas as pd

from anomaly.evaluation import AnomalyEvent, point_labels


def rule_detections(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """설비별 규칙 판정 tick 배열 (오름차순), alarm_code 존재 tick을 판정으로 간주"""
    detections: dict[str, np.ndarray] = {}
    for equipment_id, group in frame.groupby("equipment_id"):
        ticks = group.loc[group["alarm_code"].notna(), "tick"].to_numpy()
        detections[str(equipment_id)] = np.sort(ticks)
    return detections


def equipment_n_ticks(frame: pd.DataFrame) -> dict[str, int]:
    """설비별 총 관측 tick 수"""
    return {str(k): int(v) for k, v in frame.groupby("equipment_id").size().items()}


def rule_point_scores(
    frame: pd.DataFrame, events: list[AnomalyEvent]
) -> tuple[np.ndarray, np.ndarray]:
    """AUC-PR용 포인트 점수·라벨 전 설비 연결

    규칙 레이어는 연속 점수가 없어 판정 여부(0/1)를 점수로 사용, 단일 운영점 참고치
    """
    scores: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    for equipment_id, group in frame.groupby("equipment_id"):
        ordered = group.sort_values("tick")
        scores.append(ordered["alarm_code"].notna().to_numpy(dtype=float))
        labels.append(point_labels(str(equipment_id), len(ordered), events))
    return np.concatenate(scores), np.concatenate(labels)
