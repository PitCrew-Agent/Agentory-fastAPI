"""작업 로그 라우터 소유자 식별자 회귀 테스트 (NEW_LOOP01_WORKLOG01)

세션 사용자 dict에 없는 키(sub) 참조로 생성·수정·삭제가 500 나던 회귀 방지
실제 _session_user 출력으로 라우터를 호출해 존재하는 키만 쓰는지 DB 없이 검증
"""

from datetime import UTC, datetime

import pytest

from agentory.modules.auth.middleware import _session_user
from agentory.modules.worklog import router as wl_router
from agentory.modules.worklog.schemas import (
    WorkLogCreate,
    WorkLogItem,
    WorkLogStatus,
    WorkLogType,
    WorkLogUpdate,
)

S = datetime(2020, 3, 3, 2, 0, tzinfo=UTC)


def _user():
    # 세션 쿠키에서 복원되는 실제 사용자 dict (sub 키 없음)
    return _session_user(
        {
            "user_id": "7",
            "email": "owner@test",
            "name": "홍길동",
            "role": "field_engineer",
            "status": "active",
        }
    )


def _item(owner_sub: str, worker_name: str) -> WorkLogItem:
    return WorkLogItem(
        id=1,
        work_type=WorkLogType.REGULAR,
        worker_name=worker_name,
        started_at=S,
        plan="점검 완료",
        status=WorkLogStatus.PENDING,
        created_at=S,
    )


@pytest.mark.asyncio
async def test_create_uses_session_user_keys(monkeypatch):
    # 라우터가 세션 dict에 실제 존재하는 키로 소유자·진행자를 넘겨야 함 (sub 참조 시 KeyError)
    captured = {}

    async def fake_create(session, payload, *, owner_sub, worker_name):
        captured["owner_sub"] = owner_sub
        captured["worker_name"] = worker_name
        return _item(owner_sub, worker_name)

    monkeypatch.setattr(wl_router.service, "create_work_log", fake_create)
    payload = WorkLogCreate(work_type=WorkLogType.REGULAR, started_at=S, plan="점검 완료")
    result = await wl_router.create_work_log(payload, user=_user(), session=None)

    # 라우터가 ApiResponse로 감싸므로 실제 항목은 result.result
    assert result.result.worker_name == "홍길동"
    assert captured["owner_sub"] == "owner@test"  # email을 소유자 식별자로 사용
    assert captured["worker_name"] == "홍길동"


@pytest.mark.asyncio
async def test_update_uses_session_user_keys(monkeypatch):
    captured = {}

    async def fake_update(session, work_log_id, payload, *, requester_sub):
        captured["requester_sub"] = requester_sub
        return _item(requester_sub, "홍길동")

    monkeypatch.setattr(wl_router.service, "update_work_log", fake_update)
    await wl_router.update_work_log(
        1, WorkLogUpdate(status=WorkLogStatus.DONE), user=_user(), session=None
    )
    assert captured["requester_sub"] == "owner@test"


@pytest.mark.asyncio
async def test_delete_uses_session_user_keys(monkeypatch):
    captured = {}

    async def fake_delete(session, work_log_id, *, requester_sub):
        captured["requester_sub"] = requester_sub
        return True

    monkeypatch.setattr(wl_router.service, "delete_work_log", fake_delete)
    await wl_router.delete_work_log(1, user=_user(), session=None)
    assert captured["requester_sub"] == "owner@test"
