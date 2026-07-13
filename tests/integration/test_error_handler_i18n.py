"""전역 예외 핸들러·i18n 통합 테스트 (INFRA_AOP01 / INFRA_I18N01)

worklog 파일럿을 통해 도메인 예외가 통일 포맷({code, message})으로 직렬화되는지,
Accept-Language로 메시지가 번역되는지 실제 앱(ASGI)으로 검증
DB 미연결 시 스킵, 생성 없이 미존재 대상 경로만 사용해 부작용 없음
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.core.db import get_session
from agentory.main import app
from agentory.modules.auth.middleware import get_current_user

API = "/api/v1"


@pytest.fixture
async def client():
    # 인증 미들웨어의 전역 엔진 audit 접근(다른 루프) 회피 위해 테스트 동안 OIDC 비활성
    settings = get_settings()
    _saved = (settings.oidc_issuer_url, settings.oidc_client_id)
    settings.oidc_issuer_url = ""
    settings.oidc_client_id = ""
    # 전역 엔진의 이벤트 루프 바인딩 회피 위해 테스트 전용 엔진(NullPool)으로 get_session 오버라이드
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": 1,
        "email": "aop-test@test",
        "name": "테스터",
        "role": "field_engineer",
        "status": "active",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        settings.oidc_issuer_url, settings.oidc_client_id = _saved
        await engine.dispose()


async def test_not_found_uses_unified_envelope_ko(client):
    # 미존재 작업 로그 수정 -> 404 {code, message}, 기본 로케일 ko
    r = await client.patch(f"{API}/work-logs/-1", json={"status": "완료"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "작업 로그 없음: -1"
    assert "detail" not in body  # 통일 포맷은 detail 대신 message


async def test_not_found_translates_with_accept_language_en(client):
    # Accept-Language: en 이면 영어 메시지
    r = await client.patch(
        f"{API}/work-logs/-1",
        json={"status": "완료"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert r.status_code == 404
    assert r.json()["message"] == "Work log not found: -1"
