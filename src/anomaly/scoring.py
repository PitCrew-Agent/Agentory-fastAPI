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
