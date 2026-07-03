"""채팅 API 라우터 (BE_CHAT01_QUERY01 / BE_CHAT01_STREAM01)

스트리밍 이벤트 스키마는 agentory.common.events가 단일 소스, 임의 변경 금지
"""

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from agentory.core.db import SessionLocal
from agentory.modules.chat import service
from agentory.modules.chat.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
async def query(req: ChatRequest) -> ChatResponse:
    # 질의를 그래프로 처리한 전체 응답을 한 번에 반환 (비스트리밍)
    return await service.run_query(SessionLocal, req.session_id, req.message)


@router.post("/stream")
async def stream(req: ChatRequest) -> EventSourceResponse:
    # 추론 단계·최종 답변을 SSE로 실시간 전송
    async def event_generator():
        async for event in service.stream_chat(SessionLocal, req.session_id, req.message):
            yield {"event": event.type, "data": event.model_dump_json()}

    return EventSourceResponse(event_generator())
