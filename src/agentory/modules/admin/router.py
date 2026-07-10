"""관리자 라인·유저 관리 REST (BE_ADMIN01_LINE01)

전 엔드포인트 관리자 전용, 라인 CRUD와 유저별 담당 라인 지정 제공
유저 조회는 부서 대신 담당 라인 리스트를 함께 반환
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.db import get_session
from agentory.modules.admin import service
from agentory.modules.admin.schemas import (
    AdminUserItem,
    AssignLinesRequest,
    AssignManagerRequest,
    EquipmentManagerItem,
    LineCreate,
    LineItem,
    LineUpdate,
)
from agentory.modules.auth.middleware import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    # 관리자 role만 허용, 그 외 403
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


# --- 라인 마스터 CRUD ---


@router.get("/lines", response_model=list[LineItem])
async def list_lines(
    include_inactive: bool = Query(default=False),
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[LineItem]:
    # 기본은 활성 라인만, include_inactive=true면 비활성 포함
    return await service.list_lines(session, include_inactive=include_inactive)


@router.post("/lines", response_model=LineItem, status_code=status.HTTP_201_CREATED)
async def create_line(
    payload: LineCreate,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    # code 중복이면 409
    try:
        return await service.create_line(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.get("/lines/{line_id}", response_model=LineItem)
async def get_line(
    line_id: int,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    item = await service.get_line(session, line_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")
    return item


@router.patch("/lines/{line_id}", response_model=LineItem)
async def update_line(
    line_id: int,
    payload: LineUpdate,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    # 미존재 404, code 중복 409
    try:
        item = await service.update_line(session, line_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")
    return item


@router.delete("/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line(
    line_id: int,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    # 미존재 404, 삭제 시 유저 담당 라인 연결도 함께 제거
    if not await service.delete_line(session, line_id):
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")


# --- 유저 관리·담당 라인 지정 ---


@router.get("/users", response_model=list[AdminUserItem])
async def list_users(
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserItem]:
    # 유저 목록, 각 유저의 담당 라인 리스트 포함
    return await service.list_users(session)


@router.get("/users/{user_id}", response_model=AdminUserItem)
async def get_user(
    user_id: int,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserItem:
    item = await service.get_user(session, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"유저 없음: {user_id}")
    return item


@router.put("/users/{user_id}/lines", response_model=AdminUserItem)
async def assign_user_lines(
    user_id: int,
    payload: AssignLinesRequest,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserItem:
    # 담당 라인 전체 교체, 유저 미존재 404, 없는 라인 포함 시 400
    try:
        item = await service.assign_user_lines(session, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"유저 없음: {user_id}")
    return item


# --- 설비 책임자 지정 ---


@router.put("/equipment/{equipment_id}/manager", response_model=EquipmentManagerItem)
async def assign_equipment_manager(
    equipment_id: str,
    payload: AssignManagerRequest,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> EquipmentManagerItem:
    # 책임자 유저 지정(user_id=null이면 해제), 설비 미존재 404, 없는 유저 400
    try:
        item = await service.assign_equipment_manager(session, equipment_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return item
