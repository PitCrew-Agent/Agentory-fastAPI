"""장애 대응 계획 요청과 응답 스키마 (NEW_INCIDENT01_PLAN01)"""

from datetime import datetime

from pydantic import BaseModel, Field

from agentory.modules.worklog.schemas import WorkLogCreate


class IncidentPlanCreate(BaseModel):
    notification_id: int = Field(ge=1, description="대응을 시작할 알림 id")


class SensorDeviation(BaseModel):
    metric: str
    label: str
    unit: str
    baseline: float | None
    incident: float
    delta: float | None


class ManualCitation(BaseModel):
    doc_id: str
    score: float | None = None
    excerpt: str


class PlanGeneration(BaseModel):
    summary: str = Field(min_length=1)
    actions: list[str] = Field(min_length=1)
    safety_checks: list[str] = Field(default_factory=list)


class IncidentPlanResponse(BaseModel):
    notification_id: int
    equipment_id: str
    alarm_code: str
    occurred_at: datetime
    summary: str
    deviations: list[SensorDeviation]
    work_log_draft: WorkLogCreate
    citations: list[ManualCitation]
    warnings: list[str]
