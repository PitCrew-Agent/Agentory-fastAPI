"""SSE 이벤트 계약 (BE_CHAT01_STREAM01 / FE_THOUGHT01 / FE_CHAT01_STREAM 공용)

백엔드-프론트 스트리밍 계약의 단일 소스
변경 시 docs/sse-events.md, tests/contracts/ 동시 갱신 및 프론트 합의 필수

이벤트 흐름 예시:
    thought → action → observation → (반복) → answer(delta 스트림) → done
    오류 시: error → done
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class AgentName(StrEnum):
    SUPERVISOR = "supervisor"
    DATA_ANALYSIS = "data_analysis"
    KNOWLEDGE = "knowledge"
    MAINTENANCE = "maintenance"


class ThoughtEvent(BaseModel):
    """추론 단계, 에이전트가 다음 행동을 판단한 내용 (FR-09)"""

    type: Literal["thought"] = "thought"
    step: int
    agent: AgentName
    content: str


class ActionEvent(BaseModel):
    """도구 호출 단계, reason은 도구 선택 근거 노출(NEW_TRUST03_REASON01)용"""

    type: Literal["action"] = "action"
    step: int
    agent: AgentName
    tool: str
    tool_input: dict[str, Any]
    reason: str | None = None


class ObservationEvent(BaseModel):
    """도구 실행 결과 단계"""

    type: Literal["observation"] = "observation"
    step: int
    agent: AgentName
    tool: str
    content: Any


class AnswerEvent(BaseModel):
    """최종 답변 토큰 스트림 조각"""

    type: Literal["answer"] = "answer"
    delta: str


class ErrorEvent(BaseModel):
    """사용자 친화적 오류 전달 (AI_AGENT03_FALLBACK01)"""

    type: Literal["error"] = "error"
    code: str
    message: str


class Citation(BaseModel):
    """답변 근거 인용 (NEW_TRUST01_CITE01)"""

    doc_id: str
    snippet: str | None = None
    data_as_of: str | None = None  # 조회 데이터 기준 시각 (ISO 8601)


class DoneEvent(BaseModel):
    """스트림 종료, grounded는 Grounding 자가 검증 결과 (NEW_TRUST02_GROUND01)

    suggested_questions는 후속 추천 질문 (BE_CHAT02_SUGGEST01)
    """

    type: Literal["done"] = "done"
    citations: list[Citation] = []
    grounded: bool | None = None
    suggested_questions: list[str] = []


SSEEvent = Annotated[
    ThoughtEvent | ActionEvent | ObservationEvent | AnswerEvent | ErrorEvent | DoneEvent,
    Field(discriminator="type"),
]

sse_event_adapter: TypeAdapter[SSEEvent] = TypeAdapter(SSEEvent)


class NotificationEvent(BaseModel):
    """알림 스트림 이벤트 (NEW_PROACT01_ALERT01)

    챗 스트림(SSEEvent)과 별개 계약, GET /notifications/stream에서 신규 알림당 1건 방출
    """

    type: Literal["notification"] = "notification"
    id: int
    occurred_at: str  # 발생 시각 (ISO 8601)
    equipment_id: str
    alarm_code: str
    message: str
    is_read: bool


notification_event_adapter: TypeAdapter[NotificationEvent] = TypeAdapter(NotificationEvent)
