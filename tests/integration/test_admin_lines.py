"""관리자 라인·담당 라인 통합 테스트 (BE_ADMIN01_LINE01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵, teardown rollback으로 미오염
라인 CRUD, code 중복, 유저 담당 라인 지정·교체·연쇄 삭제 검증
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.common.exceptions import ConflictError, ValidationError
from agentory.core.config import get_settings
from agentory.modules.admin import repository, service
from agentory.modules.admin.schemas import AssignLinesRequest, LineCreate, LineUpdate
from agentory.modules.auth.models import User

# 실데이터와 겹치지 않는 테스트 전용 식별자
CODE_A = "ZZZ-LINE-A"
CODE_B = "ZZZ-LINE-B"


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _make_line(session, code=CODE_A, name="테스트 라인", **over):
    return await repository.create_line(
        session,
        code=code,
        name=name,
        description=over.get("description"),
        display_order=over.get("display_order"),
    )


async def _make_user(session, email="zzz-admin-tester@example.com"):
    user = User(email=email, name="ZZZ-USER", role="field_engineer")
    session.add(user)
    await session.flush()
    return user


async def test_create_and_get_line(session):
    created = await _make_line(session, display_order=5)
    fetched = await repository.get_line(session, created["id"])
    assert fetched["code"] == CODE_A
    assert fetched["status"] == "active"
    assert fetched["display_order"] == 5


async def test_create_duplicate_code_raises(session):
    await _make_line(session)
    await session.flush()
    with pytest.raises(ConflictError):
        await service.create_line(session, LineCreate(code=CODE_A, name="중복"))


async def test_list_excludes_inactive_by_default(session):
    active = await _make_line(session, code=CODE_A)
    inactive = await _make_line(session, code=CODE_B)
    await repository.update_line(session, inactive["id"], {"status": "inactive"})
    codes_default = {
        r["code"] for r in await repository.list_lines(session, include_inactive=False)
    }
    codes_all = {r["code"] for r in await repository.list_lines(session, include_inactive=True)}
    assert active["code"] in codes_default
    assert inactive["code"] not in codes_default
    assert inactive["code"] in codes_all


async def test_update_partial_keeps_other_fields(session):
    created = await _make_line(session, name="원본")
    updated = await repository.update_line(session, created["id"], {"description": "설명"})
    assert updated["description"] == "설명"
    assert updated["name"] == "원본"


async def test_service_update_not_found(session):
    result = await service.update_line(session, -1, LineUpdate(name="없음"))
    assert result is None


async def test_assign_lines_replaces_and_refs(session):
    user = await _make_user(session)
    line_a = await _make_line(session, code=CODE_A, display_order=1)
    line_b = await _make_line(session, code=CODE_B, display_order=2)

    # 두 라인 지정
    await repository.replace_user_lines(session, user.id, [line_a["id"], line_b["id"]])
    refs = await repository.list_user_line_refs(session, user.id)
    assert [r["code"] for r in refs] == [CODE_A, CODE_B]  # display_order 정렬

    # 한 라인으로 교체 (기존 전체 삭제 후 재설정)
    await repository.replace_user_lines(session, user.id, [line_b["id"]])
    refs2 = await repository.list_user_line_refs(session, user.id)
    assert [r["code"] for r in refs2] == [CODE_B]


async def test_assign_unknown_line_raises(session):
    user = await _make_user(session, email="zzz-admin-tester2@example.com")
    with pytest.raises(ValidationError):
        await service.assign_user_lines(session, user.id, AssignLinesRequest(line_ids=[-1]))


async def test_assign_user_not_found(session):
    result = await service.assign_user_lines(session, -1, AssignLinesRequest(line_ids=[]))
    assert result is None


async def test_delete_line_cascades_assignment(session):
    user = await _make_user(session, email="zzz-admin-tester3@example.com")
    line = await _make_line(session)
    await repository.replace_user_lines(session, user.id, [line["id"]])
    assert await repository.list_user_line_refs(session, user.id)  # 지정됨

    await repository.delete_line(session, line["id"])
    assert await repository.list_user_line_refs(session, user.id) == []  # 연결 연쇄 삭제
