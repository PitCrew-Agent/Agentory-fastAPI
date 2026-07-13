"""관리자 라인·담당 라인 서비스 (BE_ADMIN01_LINE01)

code 중복은 ValueError, 대상 미존재는 LookupError로 신호 (라우터에서 409/404 변환)
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

import base64
import binascii
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import ConflictError, ValidationError
from agentory.modules.admin import repository
from agentory.modules.admin.schemas import (
    AdminUserItem,
    AssignLinesRequest,
    AssignManagerRequest,
    EquipmentManagerItem,
    LineCreate,
    LineItem,
    LineRef,
    LineUpdate,
    RepairItem,
    RepairPage,
    RepairRequest,
)
from agentory.modules.telemetry.schemas import EquipmentManager

# 수리 이력 페이지 크기 기본값·상한
DEFAULT_REPAIR_PAGE_SIZE = 10
MAX_REPAIR_PAGE_SIZE = 50


async def create_line(session: AsyncSession, payload: LineCreate) -> LineItem:
    # code 중복 사전 검사, 있으면 ConflictError(409)
    if await repository.get_line_by_code(session, payload.code):
        raise ConflictError("error.line.code_conflict", params={"code": payload.code})
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
    # 미존재는 None(404), code 변경 시 타 라인과 중복이면 ConflictError(409)
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
            raise ConflictError("error.line.code_conflict", params={"code": new_code})
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
    # 유저 미존재는 None(404), 없는 라인 포함 시 ValidationError(400)
    user = await repository.get_user(session, user_id)
    if user is None:
        return None
    requested = list(dict.fromkeys(payload.line_ids))  # 중복 제거
    if requested:
        found = await repository.existing_line_ids(session, requested)
        missing = [lid for lid in requested if lid not in found]
        if missing:
            raise ValidationError("error.line.unknown", params={"ids": missing})
    await repository.replace_user_lines(session, user_id, requested)
    await session.commit()
    refs = await repository.list_user_line_refs(session, user_id)
    return AdminUserItem(**user, lines=[LineRef(**r) for r in refs])


async def assign_equipment_manager(
    session: AsyncSession, equipment_id: str, payload: AssignManagerRequest
) -> EquipmentManagerItem | None:
    # 설비 미존재는 None(404), 없는 유저 지정 시 ValidationError(400), None이면 책임자 해제
    manager = None
    if payload.user_id is not None:
        user = await repository.get_user(session, payload.user_id)
        if user is None:
            raise ValidationError("error.user.unknown", params={"id": payload.user_id})
        manager = EquipmentManager(id=user["id"], name=user["name"], email=user["email"])
    updated = await repository.set_equipment_manager(session, equipment_id, payload.user_id)
    if not updated:
        return None
    await session.commit()
    return EquipmentManagerItem(equipment_id=equipment_id, manager=manager)


def _encode_repair_cursor(repaired_at: datetime, repair_id: int) -> str:
    # 커서는 마지막 항목의 (수리시각, id)를 base64로 감싼 불투명 토큰
    raw = f"{repaired_at.isoformat()}|{repair_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_repair_cursor(cursor: str) -> tuple[datetime, int]:
    # 잘못된 커서는 ValueError (라우터에서 400)
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        at_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(at_str), int(id_str)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("error.cursor.invalid", params={"cursor": cursor}) from exc


async def repair_equipment(
    session: AsyncSession,
    equipment_id: str,
    payload: RepairRequest,
    *,
    repaired_by: int | None,
) -> RepairItem | None:
    # 설비 수리 처리(NEW_REPAIR01_REPAIR01), 설비 미존재는 None(404)
    row = await repository.create_repair(
        session, equipment_id=equipment_id, repaired_by=repaired_by, note=payload.note
    )
    if row is None:
        return None
    await session.commit()
    return RepairItem(**row)


async def list_repairs(
    session: AsyncSession,
    *,
    equipment_id: str | None = None,
    repaired_by: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    before: str | None = None,
    limit: int = DEFAULT_REPAIR_PAGE_SIZE,
) -> RepairPage:
    # 수리 작업 현황 목록(NEW_REPAIR01_HISTORY01), 수리 역순 키셋 커서 페이지네이션
    cursor = _decode_repair_cursor(before) if before else None
    page_size = max(1, min(limit, MAX_REPAIR_PAGE_SIZE))
    rows = await repository.fetch_repairs_page(
        session,
        equipment_id=equipment_id,
        repaired_by=repaired_by,
        start=start,
        end=end,
        before=cursor,
        limit=page_size + 1,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        _encode_repair_cursor(items[-1]["repaired_at"], items[-1]["id"])
        if has_more and items
        else None
    )
    return RepairPage(
        items=[RepairItem(**row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_equipment_repairs(
    session: AsyncSession,
    equipment_id: str,
    *,
    before: str | None = None,
    limit: int = DEFAULT_REPAIR_PAGE_SIZE,
) -> RepairPage | None:
    # 특정 설비 수리 이력, 설비 미존재는 None(404)
    if not await repository.equipment_exists(session, equipment_id):
        return None
    return await list_repairs(session, equipment_id=equipment_id, before=before, limit=limit)
