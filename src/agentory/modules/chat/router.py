"""채팅 API 라우터 (BE_CHAT01_QUERY01 / BE_CHAT01_STREAM01)

스트리밍 이벤트 스키마는 agentory.common.events가 단일 소스, 임의 변경 금지
"""

from typing import Any

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from agentory.common.exceptions import NotFoundError
from agentory.core.db import SessionLocal, get_session
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.chat import service
from agentory.modules.chat.schemas import (
    ChatRequest,
    ChatResponse,
    ChatSessionDetail,
    ChatSessionSummary,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse, summary="Tory 질의 (비스트리밍)")
async def query(
    req: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> ChatResponse:
    """Tory 질의 최종 응답 (비스트리밍)"""
    # 세션 소유자는 로그인 사용자로 기록 (세션 쿠키 기반 user dict, sub 미포함)
    return await service.run_query(
        SessionLocal,
        req.session_id,
        req.message,
        user_sub=user["email"],
        equipment_id=req.equipment_id,
    )


@router.get(
    "/sessions",
    response_model=list[ChatSessionSummary],
    summary="대화 히스토리 목록 조회",
)
async def list_sessions(
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ChatSessionSummary]:
    """본인 대화 세션 목록 (최신순, 장비·제목·개수 포함)"""
    return await service.list_sessions(session, user["email"])


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetail,
    summary="대화 상세 조회",
    responses={404: {"description": "세션이 없거나 본인 소유가 아님"}},
)
async def get_session_detail(
    session_id: str = Path(examples=["1e4b1c2a-9b3d-4a1f-8c2e-2b7f9a0c1d34"]),
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChatSessionDetail:
    """본인 대화 세션 전체 메시지 (시간순)"""
    detail = await service.get_session_detail(session, session_id, user["email"])
    if detail is None:
        raise NotFoundError("error.chat_session.not_found", params={"id": session_id})
    return detail


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="대화 세션 삭제",
    responses={
        204: {"description": "삭제 성공 (본문 없음)"},
        404: {"description": "세션이 없거나 본인 소유가 아님"},
    },
)
async def delete_session(
    session_id: str = Path(examples=["1e4b1c2a-9b3d-4a1f-8c2e-2b7f9a0c1d34"]),
    user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """본인 대화 세션을 히스토리에서 삭제 (soft delete)"""
    deleted = await service.delete_session(session, session_id, user["email"])
    if not deleted:
        raise NotFoundError("error.chat_session.not_found", params={"id": session_id})


@router.post(
    "/stream",
    summary="Tory 질의 (SSE 스트리밍)",
    responses={
        200: {
            "description": "text/event-stream, 추론 단계·답변 델타·완료 이벤트를 순차 전송",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream(
    req: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> EventSourceResponse:
    """Tory 질의 진행·답변 SSE 스트림"""

    async def event_generator():
        async for event in service.stream_chat(
            SessionLocal,
            req.session_id,
            req.message,
            user_sub=user["email"],
            equipment_id=req.equipment_id,
        ):
            yield {"event": event.type, "data": event.model_dump_json()}

    return EventSourceResponse(event_generator())
