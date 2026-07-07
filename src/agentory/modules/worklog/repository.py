"""작업 로그 레포지토리 (NEW_LOOP01_WORKLOG01)

미삭제(deleted_at IS NULL) 행만 조회, 삭제는 soft delete
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.worklog.models import WorkLog


def _to_dict(row: WorkLog) -> dict[str, Any]:
    # owner_sub는 소유권 검증용으로 포함, 응답 스키마(WorkLogItem)에선 무시됨
    return {
        "id": row.id,
        "owner_sub": row.owner_sub,
        "worker_name": row.worker_name,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "content": row.content,
        "status": row.status,
        "created_at": row.created_at,
    }


async def create_work_log(
    session: AsyncSession,
    *,
    owner_sub: str,
    worker_name: str,
    started_at: datetime,
    ended_at: datetime | None,
    content: str,
    status: str,
) -> dict[str, Any]:
    # 작업 로그 1건 생성
    row = WorkLog(
        owner_sub=owner_sub,
        worker_name=worker_name,
        started_at=started_at,
        ended_at=ended_at,
        content=content,
        status=status,
    )
    session.add(row)
    await session.flush()
    return _to_dict(row)


async def list_work_logs(session: AsyncSession) -> list[dict[str, Any]]:
    # 미삭제 작업 로그를 시작 시각 역순 (프론트가 날짜별 그룹)
    stmt = (
        select(WorkLog)
        .where(WorkLog.deleted_at.is_(None))
        .order_by(WorkLog.started_at.desc(), WorkLog.id.desc())
    )
    rows = await session.scalars(stmt)
    return [_to_dict(r) for r in rows]


async def get_work_log(session: AsyncSession, work_log_id: int) -> dict[str, Any] | None:
    # 미삭제 단건 조회, 없으면 None
    stmt = select(WorkLog).where(WorkLog.id == work_log_id, WorkLog.deleted_at.is_(None))
    row = await session.scalar(stmt)
    return _to_dict(row) if row else None


async def update_work_log(
    session: AsyncSession, work_log_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    # 전달된 필드만 반영, 대상(미삭제) 없으면 None
    stmt = select(WorkLog).where(WorkLog.id == work_log_id, WorkLog.deleted_at.is_(None))
    row = await session.scalar(stmt)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    return _to_dict(row)


async def soft_delete_work_log(session: AsyncSession, work_log_id: int) -> None:
    # deleted_at 표시(soft delete), 조회에서 제외됨
    await session.execute(
        update(WorkLog)
        .where(WorkLog.id == work_log_id, WorkLog.deleted_at.is_(None))
        .values(deleted_at=func.now())
    )
