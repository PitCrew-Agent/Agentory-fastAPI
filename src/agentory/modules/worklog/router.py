"""작업 로그 REST (NEW_LOOP01_WORKLOG01)

체크리스트 "점검 결과를 작업 로그에 등록" 연계
목록·작성은 인증 사용자 누구나, 수정·삭제는 소유자(작성자)만
진행자·소유자는 로그인 사용자에서 자동 기록
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.response import ApiResponse
from agentory.core.db import get_session
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.worklog import service
from agentory.modules.worklog.schemas import WorkLogCreate, WorkLogItem, WorkLogUpdate

router = APIRouter(prefix="/work-logs", tags=["work-logs"])


@router.get("", response_model=ApiResponse[list[WorkLogItem]], summary="작업 로그 목록 조회")
async def list_work_logs(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[WorkLogItem]]:
    """미삭제 작업 로그 목록 (시작 시각 역순)"""
    return ApiResponse.ok(await service.list_work_logs(session))


@router.post(
    "",
    response_model=ApiResponse[WorkLogItem],
    status_code=status.HTTP_201_CREATED,
    summary="작업 로그 작성",
)
async def create_work_log(
    payload: WorkLogCreate,
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[WorkLogItem]:
    """작업 로그 작성 (작성자·진행자 자동 기록)"""
    # 소유자 식별자는 세션 사용자 dict의 email (sub 미포함, chat 모듈과 동일 기준)
    item = await service.create_work_log(
        session, payload, owner_sub=user["email"], worker_name=user["name"]
    )
    return ApiResponse.ok(item)


@router.patch(
    "/{work_log_id}",
    response_model=ApiResponse[WorkLogItem],
    summary="작업 로그 수정 (작성자만)",
    responses={
        403: {"description": "본인 작업 로그만 수정 가능"},
        404: {"description": "작업 로그가 존재하지 않음"},
    },
)
async def update_work_log(
    work_log_id: Annotated[int, Path(examples=[15])],
    payload: WorkLogUpdate,
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[WorkLogItem]:
    """작업 로그 부분 수정 (작성자만)"""
    # 미존재·권한 오류는 서비스가 도메인 예외로 raise, 전역 핸들러가 통일 포맷 응답
    item = await service.update_work_log(session, work_log_id, payload, requester_sub=user["email"])
    return ApiResponse.ok(item)


@router.delete(
    "/{work_log_id}",
    response_model=ApiResponse[None],
    summary="작업 로그 삭제 (작성자만)",
    responses={
        403: {"description": "본인 작업 로그만 삭제 가능"},
        404: {"description": "작업 로그가 존재하지 않음"},
    },
)
async def delete_work_log(
    work_log_id: int = Path(examples=[15]),
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[None]:
    """작업 로그 삭제 (작성자만, soft delete)"""
    # 미존재·권한 오류는 서비스가 도메인 예외로 raise, 전역 핸들러가 통일 포맷 응답
    await service.delete_work_log(session, work_log_id, requester_sub=user["email"])
    return ApiResponse.ok()
