"""작업 로그 스키마 단위 테스트 (NEW_LOOP01_WORKLOG01)"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentory.modules.worklog.schemas import (
    WorkLogCreate,
    WorkLogStatus,
    WorkLogType,
    WorkLogUpdate,
)

S = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)
E = datetime(2026, 7, 6, 15, 30, tzinfo=UTC)


def test_create_defaults_to_pending():
    # 상태 미지정 시 대기
    wl = WorkLogCreate(work_type=WorkLogType.REGULAR, started_at=S, ended_at=E, content="점검")
    assert wl.status == WorkLogStatus.PENDING


def test_create_allows_open_ended():
    # 진행중이면 종료 미정 허용
    wl = WorkLogCreate(
        work_type=WorkLogType.EMERGENCY,
        started_at=S,
        content="진행 중 작업",
        status=WorkLogStatus.IN_PROGRESS,
    )
    assert wl.ended_at is None


def test_create_rejects_end_before_start():
    # 종료가 시작보다 앞서면 거부
    with pytest.raises(ValidationError):
        WorkLogCreate(work_type=WorkLogType.REPAIR, started_at=E, ended_at=S, content="시간 역전")


def test_create_rejects_blank_content():
    with pytest.raises(ValidationError):
        WorkLogCreate(work_type=WorkLogType.REGULAR, started_at=S, content="")


def test_create_requires_work_type():
    # 작업 유형은 필수 (미지정 시 거부)
    with pytest.raises(ValidationError):
        WorkLogCreate(started_at=S, content="유형 없음")


def test_create_rejects_unknown_work_type():
    # 정의되지 않은 유형은 거부 (단일 선택 enum)
    with pytest.raises(ValidationError):
        WorkLogCreate(work_type="점검아님", started_at=S, content="잘못된 유형")


def test_update_exclude_unset_only_provided():
    # 부분 수정: 전달 필드만 반영 (service가 의존하는 동작)
    upd = WorkLogUpdate(status=WorkLogStatus.DONE)
    assert upd.model_dump(exclude_unset=True) == {"status": WorkLogStatus.DONE}
