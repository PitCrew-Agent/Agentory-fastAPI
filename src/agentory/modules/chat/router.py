"""채팅 API 라우터 (BE_CHAT01_QUERY01 / BE_CHAT01_STREAM01)

스트리밍 이벤트 스키마는 agentory.common.events가 단일 소스, 임의 변경 금지
"""

from typing import Any

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from agentory.core.db import SessionLocal
from agentory.modules.auth.middleware import get_current_user
from agentory.modules.chat import service
from agentory.modules.chat.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
async def query(
    req: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> ChatResponse:
    # 질의를 그래프로 처리한 전체 응답을 한 번에 반환 (비스트리밍)
    # 세션 소유자는 로그인 사용자로 기록 (세션 쿠키 기반 user dict, sub 미포함)
    return await service.run_query(
        SessionLocal,
        req.session_id,
        req.message,
        user_sub=user["email"],
        equipment_id=req.equipment_id,
    )


@router.post("/stream")
async def stream(
    req: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> EventSourceResponse:
    # 추론 단계·최종 답변을 SSE로 실시간 전송
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
