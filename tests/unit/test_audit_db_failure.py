"""감사 로그 DB 장애 격리 단위 테스트 (INFRA_AOP01, DEV_DATABASE)

DB 적재 실패가 요청 처리를 중단시키지 않고 Redis 캐시·경고 기록으로 이어지는지 검증
"""

from types import SimpleNamespace

import agentory.modules.auth.middleware as mw


def _fake_request():
    return SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path="/api/v1/auth/me"),
        headers={},
        client=SimpleNamespace(host="10.0.0.1"),
        state=SimpleNamespace(),
    )


async def test_db_failure_does_not_break_request(monkeypatch):
    cached: list[dict] = []

    def broken_session_factory():
        raise RuntimeError('password authentication failed for user "agentory"')

    async def fake_cache(event):
        cached.append(event)

    monkeypatch.setattr(mw, "SessionLocal", broken_session_factory)
    monkeypatch.setattr(mw, "cache_audit_log", fake_cache)
    settings = mw.get_settings()
    monkeypatch.setattr(settings, "audit_log_db_enabled", True)

    request = _fake_request()
    # DB가 죽어도 예외 전파 없이 반환되어야 함
    await mw.write_audit_event(request, action="HTTP_REQUEST", status_code=401, success=False)

    assert "password authentication failed" in request.state.audit_db_error
    # DB 실패와 무관하게 Redis 캐시 적재는 계속 시도
    assert cached and cached[0]["action"] == "HTTP_REQUEST"
    assert "id" not in cached[0]


async def test_db_success_records_id(monkeypatch):
    cached: list[dict] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, obj):
            obj.id = 42

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def fake_cache(event):
        cached.append(event)

    monkeypatch.setattr(mw, "SessionLocal", FakeSession)
    monkeypatch.setattr(mw, "cache_audit_log", fake_cache)
    settings = mw.get_settings()
    monkeypatch.setattr(settings, "audit_log_db_enabled", True)

    request = _fake_request()
    await mw.write_audit_event(request, action="HTTP_REQUEST", status_code=200, success=True)

    assert cached[0]["id"] == 42
    assert not hasattr(request.state, "audit_db_error")
