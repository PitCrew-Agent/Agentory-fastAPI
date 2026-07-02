"""채팅 API 라우터 (BE_CHAT01_QUERY01 / BE_CHAT01_STREAM01)

스트리밍 이벤트 스키마는 agentory.common.events가 단일 소스, 임의 변경 금지
"""

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from agentory.common.events import DoneEvent
from agentory.modules.chat.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
async def query(req: ChatRequest) -> ChatResponse:
    """사용자 질의를 Agent 오케스트레이터에 전달 후 최종 답변 반환"""
    # TODO(채팅 API 담당): 세션 이력 로드 → Supervisor 실행 → 답변 반환
    raise HTTPException(status_code=501, detail="BE_CHAT01_QUERY01 미구현")


@router.get("/stream/{session_id}")
async def stream(session_id: str) -> EventSourceResponse:
    """추론 단계·최종 답변 SSE 실시간 전송"""

    async def event_generator():
        # TODO(채팅 API 담당): Supervisor 이벤트 큐 구독 후 SSEEvent 순서대로 yield
        done = DoneEvent()
        yield {"event": done.type, "data": done.model_dump_json()}

    return EventSourceResponse(event_generator())
