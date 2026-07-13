"""쓰기 작업 감사 의존성 단위 테스트 (INFRA_AOP01)

audit(action) 의존성이 성공·실패에 맞춰 write_audit_event를 호출하는지 직접 구동 검증
"""

from types import SimpleNamespace

import pytest

import agentory.modules.auth.audit as auditmod
from agentory.common.exceptions import PermissionDeniedError


def _fake_request(user):
    return SimpleNamespace(state=SimpleNamespace(user=user))


async def test_audit_records_success(monkeypatch):
    calls = []

    async def fake(request, *, action, status_code, success, user_id=None, error_message=None):
        calls.append((action, success, status_code, user_id))

    monkeypatch.setattr(auditmod, "write_audit_event", fake)
    gen = auditmod.audit("WORKLOG_CREATE")(_fake_request({"user_id": 9}))
    await gen.__anext__()  # yield 지점까지 진입
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()  # yield 이후 성공 감사 실행
    assert calls == [("WORKLOG_CREATE", True, 200, 9)]


async def test_audit_records_failure_with_status(monkeypatch):
    calls = []

    async def fake(request, *, action, status_code, success, user_id=None, error_message=None):
        calls.append((action, success, status_code, user_id))

    monkeypatch.setattr(auditmod, "write_audit_event", fake)
    # 인증 미들웨어가 user를 안 넣은 경우 user_id는 None
    gen = auditmod.audit("EQUIPMENT_REPAIR")(_fake_request(None))
    await gen.__anext__()
    # 도메인 예외(http_status=403)를 주입하면 실패 감사 후 재전파
    with pytest.raises(PermissionDeniedError):
        await gen.athrow(PermissionDeniedError("error.forbidden"))
    assert calls == [("EQUIPMENT_REPAIR", False, 403, None)]
