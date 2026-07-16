"""watcher.fit 순수 로직 단위 테스트"""

from datetime import UTC, datetime

from agentory.modules.watcher.fit import _effective_since

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)


def test_effective_since_takes_later_bound():
    # 최근성 경계와 정비 시점 중 늦은 쪽 (정비 이전 열화 정상 제외)
    assert _effective_since(T1, T2) == T2
    assert _effective_since(T2, T1) == T2


def test_effective_since_handles_none():
    assert _effective_since(None, T2) == T2
    assert _effective_since(T1, None) == T1
    assert _effective_since(None, None) is None
