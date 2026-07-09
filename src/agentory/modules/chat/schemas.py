"""채팅 API 요청/응답 스키마 (BE_CHAT01_QUERY01)"""

from typing import Any

from pydantic import BaseModel, Field

from agentory.common.events import Citation


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="사용자 자연어 질의")
    session_id: str = Field(description="멀티턴 대화 세션 ID")
    # 대시보드에서 선택된 설비 ID, 챗봇이 현재 설비를 컨텍스트로 인지 (NEW_TWIN01_CHATCTX01)
    # 미지정 시 특정 설비 컨텍스트 없이 처리, 대시보드 첫 화면은 기본 선택 설비를 전달
    equipment_id: str | None = Field(default=None, description="선택된 설비 ID (예: EQP-A01)")


class ReasoningStep(BaseModel):
    """비스트리밍 응답에 포함되는 추론 기록 요약"""

    step: int
    thought: str | None = None
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    observation: Any | None = None


class ChatResponse(BaseModel):
    answer: str
    reasoning_steps: list[ReasoningStep] = []
    citations: list[Citation] = []
    suggested_questions: list[str] = []  # 후속 추천 질문 (BE_CHAT02_SUGGEST01)
