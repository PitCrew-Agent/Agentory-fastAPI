"""작업 로그 서비스 (NEW_LOOP01_WORKLOG01)

수정·삭제는 소유자(owner_sub)만 허용, 비소유자는 PermissionError(라우터에서 403)
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

from datetime import UTC, datetime

from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import NotFoundError, PermissionDeniedError
from agentory.modules.admin import repository as admin_repository
from agentory.modules.telemetry.models import EquipmentMaster
from agentory.modules.worklog import repository
from agentory.modules.worklog.schemas import (
    WorkLogComplete,
    WorkLogCreate,
    WorkLogItem,
    WorkLogType,
    WorkLogUpdate,
)

# 완료 시 도메인 이력 적재 분기 (NEW_LOOP01_WORKLOG01)
# 수리류는 수리 이력(equipment_repairs), 점검류는 설비 최신 점검일 갱신, 기타는 완료 시각만
REPAIR_TYPES = frozenset({WorkLogType.EMERGENCY, WorkLogType.REPAIR})  # 긴급수리·수리점검
INSPECTION_TYPES = frozenset({WorkLogType.REGULAR, WorkLogType.PREVENTIVE})  # 정기점검·예방점검


async def create_work_log(
    session: AsyncSession, payload: WorkLogCreate, *, owner_sub: str, worker_name: str
) -> WorkLogItem:
    notification = None
    if payload.source_notification_id is not None:
        notification = await repository.get_notification_reference(
            session,
            payload.source_notification_id,
        )
        if notification is None:
            raise NotFoundError(
                "error.notification.not_found",
                params={"id": payload.source_notification_id},
            )

    # 작성자와 진행자 자동 기록 (NEW_INCIDENT01_PLAN01)
    row = await repository.create_work_log(
        session,
        owner_sub=owner_sub,
        work_type=payload.work_type,
        worker_name=worker_name,
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        plan=payload.plan,
        status=payload.status,
        source_notification_id=payload.source_notification_id,
        equipment_id=notification["equipment_id"] if notification else None,
        alarm_code=notification["alarm_code"] if notification else None,
    )
    await session.commit()
    return WorkLogItem(**row)


async def list_work_logs(session: AsyncSession) -> list[WorkLogItem]:
    rows = await repository.list_work_logs(session)
    return [WorkLogItem(**row) for row in rows]


async def update_work_log(
    session: AsyncSession, work_log_id: int, payload: WorkLogUpdate, *, requester_sub: str
) -> WorkLogItem:
    # 미존재는 NotFoundError(404), 비소유자는 PermissionDeniedError(403)
    row = await repository.get_work_log(session, work_log_id)
    if row is None:
        raise NotFoundError("error.work_log.not_found", params={"id": work_log_id})
    if row["owner_sub"] != requester_sub:
        raise PermissionDeniedError("error.work_log.update_forbidden")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return WorkLogItem(**row)
    updated = await repository.update_work_log(session, work_log_id, fields)
    await session.commit()
    return WorkLogItem(**updated)


async def complete_work_log(
    session: AsyncSession,
    work_log_id: int,
    payload: WorkLogComplete,
    *,
    requester_sub: str,
    repaired_by: int | None,
) -> WorkLogItem:
    # 작업 완료 처리 + 유형별 도메인 이력 적재 (NEW_LOOP01_WORKLOG01)
    # 미존재는 404, 비소유자는 403, 완료 시각은 서버 now
    row = await repository.get_work_log(session, work_log_id)
    if row is None:
        raise NotFoundError("error.work_log.not_found", params={"id": work_log_id})
    if row["owner_sub"] != requester_sub:
        raise PermissionDeniedError("error.work_log.update_forbidden")

    now = datetime.now(UTC)
    updated = await repository.complete_work_log(
        session, work_log_id, completion=payload.completion, completed_at=now
    )

    # 설비 연결된 로그만 유형별 도메인 이력에 적재
    equipment_id = row["equipment_id"]
    if equipment_id:
        work_type = WorkLogType(row["work_type"])
        if work_type in REPAIR_TYPES:
            # 수리 이력 적재, create_repair가 직전 알람 스냅샷·힐 윈도우 갱신 수행
            await admin_repository.create_repair(
                session,
                equipment_id=equipment_id,
                repaired_by=repaired_by,
                note=payload.completion,
            )
        elif work_type in INSPECTION_TYPES:
            # 설비 최신 점검일 갱신 (점검 이력은 work_logs로 대체, 별도 테이블 없음)
            await session.execute(
                sa_update(EquipmentMaster)
                .where(EquipmentMaster.equipment_id == equipment_id)
                .values(last_inspection_at=now.date())
            )

    await session.commit()
    return WorkLogItem(**updated)


async def delete_work_log(session: AsyncSession, work_log_id: int, *, requester_sub: str) -> None:
    # 미존재는 NotFoundError(404), 비소유자는 PermissionDeniedError(403), 삭제는 soft delete
    row = await repository.get_work_log(session, work_log_id)
    if row is None:
        raise NotFoundError("error.work_log.not_found", params={"id": work_log_id})
    if row["owner_sub"] != requester_sub:
        raise PermissionDeniedError("error.work_log.delete_forbidden")
    await repository.soft_delete_work_log(session, work_log_id)
    await session.commit()
