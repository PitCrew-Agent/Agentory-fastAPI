from httpx import ASGITransport, AsyncClient

from agentory.core.config import get_settings
from agentory.main import create_app
from agentory.modules.auth.schemas import AuthTokenResponse, AuthUserResponse


async def _noop_audit(*args, **kwargs) -> None:
    return None


def _token_response(
    *,
    access_token: str = "access-token",
    id_token: str = "id-token",
    refresh_token: str = "refresh-token",
) -> AuthTokenResponse:
    return AuthTokenResponse(
        token_type="Bearer",
        access_token=access_token,
        expires_in=3600,
        id_token=id_token,
        refresh_token=refresh_token,
        access_token_expires_at=123,
        id_token_expires_at=456,
        user=AuthUserResponse(
            id=1,
            email="user@example.com",
            name="User",
            role="field_engineer",
            status="active",
        ),
    )


async def test_callback_sets_opaque_session_cookie_without_token_url(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "frontend_redirect_uri", "http://localhost:5173/dashboard")
    monkeypatch.setattr(settings, "auth_session_cookie_name", "agentory_session")
    saved_payload: dict = {}

    async def fake_exchange(code: str, state: str) -> AuthTokenResponse:
        return _token_response()

    async def fake_store_auth_session(*, payload: dict, ttl_seconds: int | None = None) -> str:
        saved_payload.update(payload)
        return "opaque-session-id"

    monkeypatch.setattr("agentory.modules.auth.router.write_audit_event", _noop_audit)
    monkeypatch.setattr(
        "agentory.modules.auth.router.service.exchange_authorization_code", fake_exchange
    )
    monkeypatch.setattr("agentory.modules.auth.router.store_auth_session", fake_store_auth_session)

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        res = await client.get("/api/v1/auth/callback?code=code&state=state")

    assert res.status_code == 302
    assert res.headers["location"] == "http://localhost:5173/dashboard"
    assert "id-token" not in res.headers["location"]
    assert "access-token" not in res.headers["location"]
    assert "refresh-token" not in res.headers["location"]
    cookie = res.headers["set-cookie"]
    assert "agentory_session=opaque-session-id" in cookie
    assert "HttpOnly" in cookie
    assert "id-token" not in cookie
    assert "access-token" not in cookie
    assert "refresh-token" not in cookie
    assert saved_payload["id_token"] == "id-token"
    assert saved_payload["access_token"] == "access-token"
    assert saved_payload["refresh_token"] == "refresh-token"


async def test_cors_allows_credentials(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cors_allow_origins", "http://localhost:5173")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.options(
            "/api/v1/telemetry/lines",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert res.headers["access-control-allow-credentials"] == "true"


async def test_cors_allows_sse_fetch_headers(monkeypatch):
    # fetch 기반 SSE preflight가 Cache-Control·Last-Event-ID 비안전 헤더를 허용받는지 검증
    settings = get_settings()
    monkeypatch.setattr(settings, "cors_allow_origins", "http://localhost:5173")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.options(
            "/api/v1/chat/stream",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,cache-control,last-event-id",
            },
        )

    assert res.status_code == 200
    allowed = res.headers["access-control-allow-headers"].lower()
    assert "cache-control" in allowed
    assert "last-event-id" in allowed
    assert res.headers["access-control-allow-credentials"] == "true"


async def test_me_reads_opaque_session(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_issuer_url", "https://issuer.example")
    monkeypatch.setattr(settings, "oidc_client_id", "client-id")
    monkeypatch.setattr(settings, "auth_session_cookie_name", "agentory_session")

    async def fake_get_auth_session(session_id: str) -> dict:
        assert session_id == "opaque-session-id"
        return {
            "user_id": "7",
            "email": "user@example.com",
            "name": "User",
            "role": "field_engineer",
            "status": "active",
        }

    monkeypatch.setattr("agentory.modules.auth.middleware.get_auth_session", fake_get_auth_session)
    monkeypatch.setattr("agentory.modules.auth.middleware.write_audit_event", _noop_audit)

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"agentory_session": "opaque-session-id"},
    ) as client:
        res = await client.get("/api/v1/auth/me")

    assert res.status_code == 200
    assert res.json()["email"] == "user@example.com"


async def test_refresh_updates_server_session_without_returning_tokens(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_session_cookie_name", "agentory_session")
    updated_payload: dict = {}

    async def fake_get_auth_session(session_id: str) -> dict:
        assert session_id == "opaque-session-id"
        return {
            "user_id": "1",
            "email": "user@example.com",
            "name": "User",
            "role": "field_engineer",
            "status": "active",
            "refresh_token": "old-refresh-token",
        }

    async def fake_refresh_tokens(*, refresh_token: str) -> AuthTokenResponse:
        assert refresh_token == "old-refresh-token"
        return _token_response(
            access_token="new-access-token",
            id_token="new-id-token",
            refresh_token="new-refresh-token",
        )

    async def fake_update_auth_session(
        session_id: str,
        *,
        payload: dict,
        ttl_seconds: int | None = None,
    ) -> bool:
        assert session_id == "opaque-session-id"
        updated_payload.update(payload)
        return True

    monkeypatch.setattr("agentory.modules.auth.router.write_audit_event", _noop_audit)
    monkeypatch.setattr("agentory.modules.auth.router.get_auth_session", fake_get_auth_session)
    monkeypatch.setattr("agentory.modules.auth.router.service.refresh_tokens", fake_refresh_tokens)
    monkeypatch.setattr(
        "agentory.modules.auth.router.update_auth_session", fake_update_auth_session
    )

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"agentory_session": "opaque-session-id"},
    ) as client:
        res = await client.post("/api/v1/auth/refresh")

    assert res.status_code == 200
    assert res.json() == {
        "id": 1,
        "email": "user@example.com",
        "name": "User",
        "role": "field_engineer",
        "status": "active",
        "lines": [],  # 담당 라인 필드 추가 (refresh는 세션 기반이라 빈 리스트)
    }
    assert "new-access-token" not in res.text
    assert "new-id-token" not in res.text
    assert "new-refresh-token" not in res.text
    assert updated_payload["access_token"] == "new-access-token"
    assert updated_payload["refresh_token"] == "new-refresh-token"


async def test_logout_deletes_server_session_and_cookie(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_issuer_url", "https://issuer.example")
    monkeypatch.setattr(settings, "oidc_client_id", "client-id")
    monkeypatch.setattr(settings, "auth_session_cookie_name", "agentory_session")
    deleted: list[str] = []

    async def fake_get_auth_session(session_id: str) -> dict:
        return {
            "user_id": "7",
            "email": "user@example.com",
            "name": "User",
            "role": "field_engineer",
            "status": "active",
        }

    async def fake_delete_auth_session(session_id: str) -> bool:
        deleted.append(session_id)
        return True

    async def fake_build_logout_url() -> str:
        return "https://issuer.example/logout"

    monkeypatch.setattr("agentory.modules.auth.middleware.get_auth_session", fake_get_auth_session)
    monkeypatch.setattr("agentory.modules.auth.middleware.write_audit_event", _noop_audit)
    monkeypatch.setattr("agentory.modules.auth.router.write_audit_event", _noop_audit)
    monkeypatch.setattr(
        "agentory.modules.auth.router.delete_auth_session", fake_delete_auth_session
    )
    monkeypatch.setattr(
        "agentory.modules.auth.router.service.build_logout_url", fake_build_logout_url
    )

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"agentory_session": "opaque-session-id"},
    ) as client:
        res = await client.post("/api/v1/auth/logout")

    assert res.status_code == 200
    assert res.json()["refresh_token_revoked"] is True
    assert deleted == ["opaque-session-id"]
    assert "agentory_session=" in res.headers["set-cookie"]
    assert "Max-Age=0" in res.headers["set-cookie"]
