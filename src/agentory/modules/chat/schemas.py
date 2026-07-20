"""채팅 API 요청/응답 스키마 (BE_CHAT01_QUERY01)"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentory.common.events import Citation


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1, description="사용자 자연어 질의", examples=["EQP-A01 최신 온도 확인해줘"]
    )
    session_id: str = Field(
        description="멀티턴 대화 세션 id (UUID), 새 대화면 클라이언트가 새 UUID 생성",
        examples=["1e4b1c2a-9b3d-4a1f-8c2e-2b7f9a0c1d34"],
    )
    # 대시보드에서 선택된 설비 ID, 챗봇이 현재 설비를 컨텍스트로 인지 (NEW_TWIN01_CHATCTX01)
    # 미지정 시 특정 설비 컨텍스트 없이 처리, 대시보드 첫 화면은 기본 선택 설비를 전달
    equipment_id: str | None = Field(
        default=None, description="대화 컨텍스트 설비 id, 대시보드 선택 설비", examples=["EQP-A01"]
    )


class ReasoningStep(BaseModel):
    """비스트리밍 응답에 포함되는 추론 기록 요약"""

    step: int = Field(description="추론 단계 순번", examples=[1])
    thought: str | None = Field(default=None, description="해당 단계의 사고 내용")
    tool: str | None = Field(default=None, description="호출한 도구명", examples=["get_telemetry"])
    tool_input: dict[str, Any] | None = Field(default=None, description="도구 입력 인자")
    observation: Any | None = Field(default=None, description="도구 실행 결과 관측값")


class ChatResponse(BaseModel):
    answer: str = Field(description="에이전트 최종 답변")
    reasoning_steps: list[ReasoningStep] = Field(default=[], description="추론 단계 기록")
    citations: list[Citation] = Field(default=[], description="답변 근거 인용 목록")
    suggested_questions: list[str] = Field(
        default=[], description="후속 추천 질문 (BE_CHAT02_SUGGEST01)"
    )


class ChatSessionSummary(BaseModel):
    # 대화 히스토리 목록 항목 (BE_CHAT03_HISTORY01), 장비 배지 + 제목 + 일시로 표시
    session_id: str = Field(description="대화 세션 id (UUID)")
    equipment_id: str | None = Field(
        default=None, description="대화 컨텍스트 설비, 목록 배지 표시용", examples=["EQP-A01"]
    )
    title: str = Field(
        description="제목 (첫 질문 요약, 앞 장비id 중복은 제거)",
        examples=["최신 온도 확인해줘"],
    )
    created_at: datetime = Field(description="세션 생성 시각")
    last_message_at: datetime | None = Field(
        default=None, description="마지막 메시지 시각, 정렬·표시용"
    )
    message_count: int = Field(description="세션 내 메시지 수", examples=[4])


class ChatMessageItem(BaseModel):
    # 대화 상세의 개별 메시지, trace는 추론 기록(steps·citations 등) 원본
    message_id: int = Field(description="메시지 id")
    role: str = Field(description="발화 주체 (user·assistant·system·tool)", examples=["user"])
    content: str = Field(description="메시지 본문")
    trace: dict[str, Any] | None = Field(
        default=None, description="assistant 메시지의 추론 기록(steps·citations 등), 없으면 null"
    )
    created_at: datetime = Field(description="메시지 시각")


class ChatSessionDetail(BaseModel):
    # 대화 상세 (BE_CHAT03_HISTORY02), 세션 메타 + 전체 메시지
    session_id: str = Field(description="대화 세션 id (UUID)")
    equipment_id: str | None = Field(
        default=None, description="대화 컨텍스트 설비", examples=["EQP-A01"]
    )
    title: str = Field(description="제목 (첫 질문 요약)")
    created_at: datetime = Field(description="세션 생성 시각")
    messages: list[ChatMessageItem] = Field(default=[], description="전체 메시지 (시간순)")
