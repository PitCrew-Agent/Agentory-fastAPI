"""장애 대응 계획 API (NEW_INCIDENT01_PLAN01)"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.response import ApiResponse
from agentory.core.db import get_session
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.incident import service
from agentory.modules.incident.schemas import IncidentPlanCreate, IncidentPlanResponse

router = APIRouter(prefix="/incident-plans", tags=["incident-plans"])


@router.post(
    "",
    response_model=ApiResponse[IncidentPlanResponse],
    summary="알림 기반 대응 계획과 작업 로그 초안 생성",
)
async def create_incident_plan(
    payload: IncidentPlanCreate,
    _: Annotated[dict[str, Any], Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse[IncidentPlanResponse]:
    item = await service.create_incident_plan(session, payload.notification_id)
    return ApiResponse.ok(item)
