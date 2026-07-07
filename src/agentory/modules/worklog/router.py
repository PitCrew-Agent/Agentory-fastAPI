"""작업 로그 REST (NEW_LOOP01_WORKLOG01)

체크리스트 "점검 결과를 작업 로그에 등록" 연계
목록·작성은 인증 사용자 누구나, 수정·삭제는 소유자(작성자)만
진행자·소유자는 로그인 사용자에서 자동 기록
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.db import get_session
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.worklog import service
from agentory.modules.worklog.schemas import WorkLogCreate, WorkLogItem, WorkLogUpdate

router = APIRouter(prefix="/work-logs", tags=["work-logs"])


@router.get("", response_model=list[WorkLogItem])
async def list_work_logs(
    session: AsyncSession = Depends(get_session),
) -> list[WorkLogItem]:
    # 미삭제 작업 로그 목록 (작업 시작 시각 역순)
    return await service.list_work_logs(session)


@router.post("", response_model=WorkLogItem, status_code=status.HTTP_201_CREATED)
async def create_work_log(
    payload: WorkLogCreate,
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkLogItem:
    # 작성자=소유자, 진행자 이름은 로그인 사용자에서 자동
    return await service.create_work_log(
        session, payload, owner_sub=user["sub"], worker_name=user["name"]
    )


@router.patch("/{work_log_id}", response_model=WorkLogItem)
async def update_work_log(
    work_log_id: int,
    payload: WorkLogUpdate,
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WorkLogItem:
    # 소유자만 수정(403), 미존재 404
    try:
        item = await service.update_work_log(
            session, work_log_id, payload, requester_sub=user["sub"]
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="본인 작업 로그만 수정 가능") from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"작업 로그 없음: {work_log_id}")
    return item


@router.delete("/{work_log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_log(
    work_log_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    # 소유자만 삭제(403, soft delete), 미존재 404
    try:
        deleted = await service.delete_work_log(session, work_log_id, requester_sub=user["sub"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="본인 작업 로그만 삭제 가능") from None
    if not deleted:
        raise HTTPException(status_code=404, detail=f"작업 로그 없음: {work_log_id}")
