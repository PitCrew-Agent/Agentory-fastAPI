"""작업 로그 서비스 (NEW_LOOP01_WORKLOG01)

수정·삭제는 소유자(owner_sub)만 허용, 비소유자는 PermissionError(라우터에서 403)
쓰기 경로는 명시적 commit (get_session은 자동 커밋 안 함)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.worklog import repository
from agentory.modules.worklog.schemas import WorkLogCreate, WorkLogItem, WorkLogUpdate


async def create_work_log(
    session: AsyncSession, payload: WorkLogCreate, *, owner_sub: str, worker_name: str
) -> WorkLogItem:
    # 작성자=소유자, 진행자 이름은 로그인 사용자에서 자동
    row = await repository.create_work_log(
        session,
        owner_sub=owner_sub,
        work_type=payload.work_type,
        worker_name=worker_name,
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        content=payload.content,
        status=payload.status,
    )
    await session.commit()
    return WorkLogItem(**row)


async def list_work_logs(session: AsyncSession) -> list[WorkLogItem]:
    rows = await repository.list_work_logs(session)
    return [WorkLogItem(**row) for row in rows]


async def update_work_log(
    session: AsyncSession, work_log_id: int, payload: WorkLogUpdate, *, requester_sub: str
) -> WorkLogItem | None:
    # 미존재는 None(404), 비소유자는 PermissionError(403)
    row = await repository.get_work_log(session, work_log_id)
    if row is None:
        return None
    if row["owner_sub"] != requester_sub:
        raise PermissionError("작업 로그 소유자만 수정 가능")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return WorkLogItem(**row)
    updated = await repository.update_work_log(session, work_log_id, fields)
    await session.commit()
    return WorkLogItem(**updated)


async def delete_work_log(session: AsyncSession, work_log_id: int, *, requester_sub: str) -> bool:
    # 미존재는 False(404), 비소유자는 PermissionError(403), 삭제는 soft delete
    row = await repository.get_work_log(session, work_log_id)
    if row is None:
        return False
    if row["owner_sub"] != requester_sub:
        raise PermissionError("작업 로그 소유자만 삭제 가능")
    await repository.soft_delete_work_log(session, work_log_id)
    await session.commit()
    return True
