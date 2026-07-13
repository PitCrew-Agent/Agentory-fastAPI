"""관리자 라인·유저 관리 REST (BE_ADMIN01_LINE01)

전 엔드포인트 관리자 전용, 라인 CRUD와 유저별 담당 라인 지정 제공
유저 조회는 부서 대신 담당 라인 리스트를 함께 반환
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
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
    RepairItem,
    RepairPage,
    RepairRequest,
)
from agentory.modules.auth.middleware import get_current_user

# 전 엔드포인트 관리자 전용, 공통 401·403 응답을 문서에 표기
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={
        401: {"description": "미인증"},
        403: {"description": "관리자 권한 필요"},
    },
)


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    # 관리자 role만 허용, 그 외 403
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


async def require_field_or_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    # 수리·수리 이력은 관리자·현장 책임자 모두 허용 (전체 설비 대상)
    if user.get("role") not in ("admin", "field_engineer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


# --- 라인 마스터 CRUD ---


@router.get("/lines", response_model=list[LineItem], summary="담당 라인 목록 조회")
async def list_lines(
    include_inactive: bool = Query(
        default=False, description="true면 비활성 라인도 포함", examples=[False]
    ),
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[LineItem]:
    """담당 라인 목록 (기본 활성만)"""
    return await service.list_lines(session, include_inactive=include_inactive)


@router.post(
    "/lines",
    response_model=LineItem,
    status_code=status.HTTP_201_CREATED,
    summary="담당 라인 생성",
    responses={409: {"description": "동일한 code의 라인이 이미 존재"}},
)
async def create_line(
    payload: LineCreate,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    """담당 라인 생성 (code 고유, 설비 line_name과 매칭)"""
    try:
        return await service.create_line(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.get(
    "/lines/{line_id}",
    response_model=LineItem,
    summary="담당 라인 상세 조회",
    responses={404: {"description": "라인이 존재하지 않음"}},
)
async def get_line(
    line_id: int = Path(examples=[3]),
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    """담당 라인 상세"""
    item = await service.get_line(session, line_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")
    return item


@router.patch(
    "/lines/{line_id}",
    response_model=LineItem,
    summary="담당 라인 수정",
    responses={
        404: {"description": "라인이 존재하지 않음"},
        409: {"description": "변경하려는 code가 다른 라인과 중복"},
    },
)
async def update_line(
    line_id: Annotated[int, Path(examples=[3])],
    payload: LineUpdate,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LineItem:
    """담당 라인 부분 수정 (status=inactive로 비활성화)"""
    try:
        item = await service.update_line(session, line_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")
    return item


@router.delete(
    "/lines/{line_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="담당 라인 삭제",
    responses={
        204: {"description": "삭제 성공 (본문 없음)"},
        404: {"description": "라인이 존재하지 않음"},
    },
)
async def delete_line(
    line_id: int = Path(examples=[3]),
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """담당 라인 삭제 (연결된 유저 담당 라인도 제거)"""
    if not await service.delete_line(session, line_id):
        raise HTTPException(status_code=404, detail=f"라인 없음: {line_id}")


# --- 유저 관리·담당 라인 지정 ---


@router.get("/users", response_model=list[AdminUserItem], summary="유저 목록 조회 (담당 라인 포함)")
async def list_users(
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserItem]:
    """유저 목록 (부서 대신 담당 라인 포함)"""
    return await service.list_users(session)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserItem,
    summary="유저 상세 조회 (담당 라인 포함)",
    responses={404: {"description": "유저가 존재하지 않음"}},
)
async def get_user(
    user_id: int = Path(examples=[7]),
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserItem:
    """유저 상세 (담당 라인 포함)"""
    item = await service.get_user(session, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"유저 없음: {user_id}")
    return item


@router.put(
    "/users/{user_id}/lines",
    response_model=AdminUserItem,
    summary="유저 담당 라인 지정 (전체 교체)",
    responses={
        400: {"description": "존재하지 않는 라인 id가 포함됨"},
        404: {"description": "유저가 존재하지 않음"},
    },
)
async def assign_user_lines(
    user_id: Annotated[int, Path(examples=[7])],
    payload: AssignLinesRequest,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserItem:
    """유저 담당 라인 전체 교체 (빈 배열이면 전부 해제)"""
    try:
        item = await service.assign_user_lines(session, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"유저 없음: {user_id}")
    return item


# --- 설비 책임자 지정 ---


@router.put(
    "/equipment/{equipment_id}/manager",
    response_model=EquipmentManagerItem,
    summary="설비 책임자 지정",
    responses={
        400: {"description": "존재하지 않는 유저 id"},
        404: {"description": "설비가 존재하지 않음"},
    },
)
async def assign_equipment_manager(
    equipment_id: Annotated[str, Path(examples=["EQP-A01"])],
    payload: AssignManagerRequest,
    _: dict[str, Any] = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> EquipmentManagerItem:
    """설비 책임자 유저 지정 (user_id=null이면 해제)"""
    try:
        item = await service.assign_equipment_manager(session, equipment_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return item


# --- 설비 수리·수리 이력 (관리자·현장 책임자 공용) ---


@router.post(
    "/equipment/{equipment_id}/repair",
    response_model=RepairItem,
    status_code=status.HTTP_201_CREATED,
    summary="설비 수리 처리",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def repair_equipment(
    equipment_id: Annotated[str, Path(examples=["EQP-A05"])],
    payload: RepairRequest,
    user: dict[str, Any] = Depends(require_field_or_admin),
    session: AsyncSession = Depends(get_session),
) -> RepairItem:
    """설비 수리 처리 (수리자는 로그인 유저 자동 기록, 힐 윈도우 동안 시뮬레이터 정상화)"""
    item = await service.repair_equipment(
        session, equipment_id, payload, repaired_by=user.get("user_id")
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return item


@router.get(
    "/repairs",
    response_model=RepairPage,
    summary="수리 작업 현황 조회 (커서 페이지네이션)",
    responses={400: {"description": "before 커서 형식이 잘못됨"}},
)
async def list_repairs(
    equipment_id: str | None = Query(
        default=None, description="지정 시 해당 설비만", examples=["EQP-A05"]
    ),
    repaired_by: int | None = Query(
        default=None, description="지정 시 해당 책임자만", examples=[7]
    ),
    start: datetime | None = Query(default=None, examples=["2026-07-13T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-13T23:59:59+09:00"]),
    before: str | None = Query(
        default=None, description="이전 페이지 마지막 항목 커서, 첫 페이지는 생략"
    ),
    limit: int = Query(
        default=service.DEFAULT_REPAIR_PAGE_SIZE,
        ge=1,
        le=service.MAX_REPAIR_PAGE_SIZE,
        examples=[10],
    ),
    _: dict[str, Any] = Depends(require_field_or_admin),
    session: AsyncSession = Depends(get_session),
) -> RepairPage:
    """수리 작업 현황, 수리 역순 커서 페이지네이션 (설비·책임자·기간 필터)"""
    try:
        return await service.list_repairs(
            session,
            equipment_id=equipment_id,
            repaired_by=repaired_by,
            start=start,
            end=end,
            before=before,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get(
    "/equipment/{equipment_id}/repairs",
    response_model=RepairPage,
    summary="설비별 수리 이력 조회",
    responses={
        400: {"description": "before 커서 형식이 잘못됨"},
        404: {"description": "설비가 존재하지 않음"},
    },
)
async def list_equipment_repairs(
    equipment_id: Annotated[str, Path(examples=["EQP-A05"])],
    before: str | None = Query(
        default=None, description="이전 페이지 마지막 항목 커서, 첫 페이지는 생략"
    ),
    limit: int = Query(
        default=service.DEFAULT_REPAIR_PAGE_SIZE,
        ge=1,
        le=service.MAX_REPAIR_PAGE_SIZE,
        examples=[10],
    ),
    _: dict[str, Any] = Depends(require_field_or_admin),
    session: AsyncSession = Depends(get_session),
) -> RepairPage:
    """특정 설비 수리 이력, 수리 역순 커서 페이지네이션"""
    try:
        page = await service.list_equipment_repairs(
            session, equipment_id, before=before, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if page is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return page
