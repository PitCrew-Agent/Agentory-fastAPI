"""이상도 점수 후처리 (EXP-006)

단발 윈도우 임계가 못 넘는 지속 저강도 신호(예: 상관 붕괴, 윈도우당 약 3sigma)를
점수 시계열 누적으로 감지. EWMA는 잡음 분산을 sqrt(alpha/(2-alpha))배로 줄여
동일 분위수 한계 대비 지속 신호의 상대 강도를 높임

한계 재캘리브레이션 필수: EWMA 점수의 분포는 원 점수와 달라, 학습 정상 시퀀스에
동일 EWMA를 적용한 분위수로 한계를 다시 산출해야 함 (run.py의 _run_windowed 참고)
"""

import numpy as np


def sustained(over_threshold: np.ndarray, k: int) -> np.ndarray:
    """연속 K윈도우 초과 확인 규칙

    over_threshold는 윈도우별 임계 초과 여부(bool), k는 발령에 필요한 연속 초과 수
    반환: 각 윈도우에서 직전 k개(자신 포함)가 모두 초과면 True (발령 인정)
    고립된 단발 이탈을 억제, 지연은 (k-1) 스트라이드만큼 증가
    k <= 1이면 원본 그대로 반환
    """
    if k <= 1:
        return over_threshold.astype(bool)
    if over_threshold.size == 0:
        return over_threshold.astype(bool)
    # 연속 True 런 길이 누적, False에서 0으로 리셋
    flags = over_threshold.astype(bool)
    run = np.zeros(flags.shape, dtype=int)
    count = 0
    for i, flag in enumerate(flags):
        count = count + 1 if flag else 0
        run[i] = count
    return run >= k


def ewma(scores: np.ndarray, alpha: float) -> np.ndarray:
    """지수 가중 이동 평균, 첫 값으로 초기화

    alpha는 (0, 1], 클수록 최신 점수 반영이 빠르고 누적 효과는 약함
    """
    if not 0.0 < alpha <= 1.0:
        raise ValueError("alpha는 (0, 1] 범위만 허용")
    if scores.size == 0:
        return scores.copy()
    out = np.empty_like(scores, dtype=float)
    acc = float(scores[0])
    for i, value in enumerate(scores):
        acc = alpha * float(value) + (1.0 - alpha) * acc
        out[i] = acc
    return out


def transition_mask(raw_scores: np.ndarray, threshold: float, settle: int) -> np.ndarray:
    """급격한 레짐 변화(전이) 구간 억제 마스크 (EXP-008)

    raw 점수가 threshold 이상인 윈도우와 이후 settle개를 억제 대상으로 표시
    점화·램프업 등 모드 전이는 raw 점수가 수천대로 급등해 진짜 이상(수 이하)과 완전 분리됨
    반환: 억제 여부 bool 배열, threshold<=0이면 억제 없음
    """
    if threshold <= 0 or raw_scores.size == 0:
        return np.zeros(raw_scores.shape, dtype=bool)
    over = raw_scores >= threshold
    mask = over.copy()
    remaining = 0
    for i in range(over.size):
        if over[i]:
            remaining = settle
        elif remaining > 0:
            mask[i] = True
            remaining -= 1
    return mask


def ewma_masked(scores: np.ndarray, alpha: float, mask: np.ndarray) -> np.ndarray:
    """마스크 구간 누적을 건너뛰는 EWMA (전이 오염 차단, EXP-008)

    억제 구간은 직전 누적값을 유지해 전이 급등이 EWMA에 흘러들지 않게 함
    마스크 해제 후 유지된 저값에서 재개하므로 전이 후 꼬리 발령 없음
    """
    if not 0.0 < alpha <= 1.0:
        raise ValueError("alpha는 (0, 1] 범위만 허용")
    if scores.size == 0:
        return scores.copy()
    # 첫 비억제 위치에서 초기화, 전이로 시작하는 세그먼트의 초기 오염(램프 첫 값) 방지
    unmasked = np.flatnonzero(~np.asarray(mask, dtype=bool))
    if unmasked.size == 0:
        return np.zeros_like(scores, dtype=float)
    out = np.empty_like(scores, dtype=float)
    acc = float(scores[unmasked[0]])
    for i, (value, suppressed) in enumerate(zip(scores, mask, strict=True)):
        if not suppressed:
            acc = alpha * float(value) + (1.0 - alpha) * acc
        out[i] = acc
    return out
