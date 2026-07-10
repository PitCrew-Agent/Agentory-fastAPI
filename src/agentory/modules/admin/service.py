"""관리자 라인·담당 라인 서비스 (BE_ADMIN01_LINE01)

code 중복은 ValueError, 대상 미존재는 LookupError로 신호 (라우터에서 409/404 변환)
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.admin import repository
from agentory.modules.admin.schemas import (
    AdminUserItem,
    AssignLinesRequest,
    LineCreate,
    LineItem,
    LineRef,
    LineUpdate,
)


async def create_line(session: AsyncSession, payload: LineCreate) -> LineItem:
    # code 중복 사전 검사, 있으면 ValueError(409)
    if await repository.get_line_by_code(session, payload.code):
        raise ValueError(f"이미 존재하는 라인 코드: {payload.code}")
    row = await repository.create_line(
        session,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        display_order=payload.display_order,
    )
    await session.commit()
    return LineItem(**row)


async def list_lines(session: AsyncSession, *, include_inactive: bool) -> list[LineItem]:
    rows = await repository.list_lines(session, include_inactive=include_inactive)
    return [LineItem(**row) for row in rows]


async def get_line(session: AsyncSession, line_id: int) -> LineItem | None:
    row = await repository.get_line(session, line_id)
    return LineItem(**row) if row else None


async def update_line(session: AsyncSession, line_id: int, payload: LineUpdate) -> LineItem | None:
    # 미존재는 None(404), code 변경 시 타 라인과 중복이면 ValueError(409)
    current = await repository.get_line(session, line_id)
    if current is None:
        return None
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return LineItem(**current)
    new_code = fields.get("code")
    if new_code and new_code != current["code"]:
        existing = await repository.get_line_by_code(session, new_code)
        if existing and existing["id"] != line_id:
            raise ValueError(f"이미 존재하는 라인 코드: {new_code}")
    updated = await repository.update_line(session, line_id, fields)
    await session.commit()
    return LineItem(**updated)


async def delete_line(session: AsyncSession, line_id: int) -> bool:
    # 미존재는 False(404), 담당 라인 연결은 FK CASCADE로 함께 정리
    deleted = await repository.delete_line(session, line_id)
    if deleted:
        await session.commit()
    return deleted


async def list_user_line_refs(session: AsyncSession, user_id: int) -> list[LineRef]:
    # 유저 1명의 담당 라인 (auth /me 등에서 사용)
    rows = await repository.list_user_line_refs(session, user_id)
    return [LineRef(**row) for row in rows]


async def list_users(session: AsyncSession) -> list[AdminUserItem]:
    # 유저 목록에 담당 라인 합류, 담당 라인은 리스트로 노출
    users = await repository.list_users(session)
    refs = await repository.line_refs_by_user(session, [u["id"] for u in users])
    return [
        AdminUserItem(**user, lines=[LineRef(**r) for r in refs.get(user["id"], [])])
        for user in users
    ]


async def get_user(session: AsyncSession, user_id: int) -> AdminUserItem | None:
    user = await repository.get_user(session, user_id)
    if user is None:
        return None
    refs = await repository.list_user_line_refs(session, user_id)
    return AdminUserItem(**user, lines=[LineRef(**r) for r in refs])


async def assign_user_lines(
    session: AsyncSession, user_id: int, payload: AssignLinesRequest
) -> AdminUserItem | None:
    # 유저 미존재는 None(404), 없는 라인 포함 시 ValueError(400)
    user = await repository.get_user(session, user_id)
    if user is None:
        return None
    requested = list(dict.fromkeys(payload.line_ids))  # 중복 제거
    if requested:
        found = await repository.existing_line_ids(session, requested)
        missing = [lid for lid in requested if lid not in found]
        if missing:
            raise ValueError(f"존재하지 않는 라인: {missing}")
    await repository.replace_user_lines(session, user_id, requested)
    await session.commit()
    refs = await repository.list_user_line_refs(session, user_id)
    return AdminUserItem(**user, lines=[LineRef(**r) for r in refs])
