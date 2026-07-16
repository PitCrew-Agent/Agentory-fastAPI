"""구간 단위 windowing 유틸 (이상 감지 실험 공통)

설비별 시계열을 오버랩드 윈도우로 절단해 모델 입력 (N, window, C) 배열 생성
윈도우 시작 tick과 이벤트 구간의 겹침 판정도 여기서 단일 소스로 유지
"""

import numpy as np


def sliding_windows(series: np.ndarray, window: int, stride: int) -> np.ndarray:
    """(T, C) 시계열을 (N, window, C) 오버랩드 윈도우 배열로 변환

    (T,) 1차원 입력은 채널 1개로 승격, 끝의 자투리 구간은 버림, T < window면 빈 배열
    """
    if window <= 0 or stride <= 0:
        raise ValueError("window·stride는 양수만 허용")
    if series.ndim == 1:
        series = series[:, None]
    n_ticks = series.shape[0]
    if n_ticks < window:
        return np.empty((0, window, series.shape[1]), dtype=series.dtype)
    starts = window_starts(n_ticks, window, stride)
    return np.stack([series[s : s + window] for s in starts])


def window_starts(n_ticks: int, window: int, stride: int) -> np.ndarray:
    """sliding_windows와 동일 절단 규칙의 윈도우 시작 tick 배열"""
    if n_ticks < window:
        return np.empty(0, dtype=int)
    return np.arange(0, n_ticks - window + 1, stride)


def window_overlaps_event(
    starts: np.ndarray, window: int, event_start: int, event_end: int
) -> np.ndarray:
    """각 윈도우가 이벤트 구간 [event_start, event_end]와 겹치는지 여부 (bool 배열)"""
    ends = starts + window - 1
    return (starts <= event_end) & (ends >= event_start)
