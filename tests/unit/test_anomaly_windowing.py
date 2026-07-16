"""anomaly.windowing 단위 테스트"""

import numpy as np
import pytest

from anomaly.windowing import sliding_windows, window_overlaps_event, window_starts


def test_sliding_windows_shape_and_overlap():
    # 10 tick x 2채널, window 4·stride 2 → 시작 0/2/4/6 총 4개
    series = np.arange(20, dtype=float).reshape(10, 2)
    windows = sliding_windows(series, window=4, stride=2)
    assert windows.shape == (4, 4, 2)
    # 오버랩 검증, 두 번째 윈도우는 tick 2부터 시작
    assert np.array_equal(windows[1], series[2:6])


def test_sliding_windows_promotes_1d_to_single_channel():
    series = np.arange(6, dtype=float)
    windows = sliding_windows(series, window=3, stride=3)
    assert windows.shape == (2, 3, 1)


def test_sliding_windows_short_series_returns_empty():
    series = np.zeros((3, 2))
    windows = sliding_windows(series, window=5, stride=1)
    assert windows.shape == (0, 5, 2)


def test_sliding_windows_rejects_nonpositive_params():
    with pytest.raises(ValueError):
        sliding_windows(np.zeros((10, 1)), window=0, stride=1)
    with pytest.raises(ValueError):
        sliding_windows(np.zeros((10, 1)), window=3, stride=0)


def test_window_starts_matches_sliding_windows():
    series = np.zeros((11, 1))
    windows = sliding_windows(series, window=4, stride=3)
    starts = window_starts(11, window=4, stride=3)
    assert len(starts) == windows.shape[0]
    assert starts.tolist() == [0, 3, 6]


def test_window_overlaps_event():
    starts = np.array([0, 3, 6, 9])
    # 이벤트 [4, 7], window 3 → 윈도우 구간 [0,2]/[3,5]/[6,8]/[9,11]
    overlaps = window_overlaps_event(starts, window=3, event_start=4, event_end=7)
    assert overlaps.tolist() == [False, True, True, False]
