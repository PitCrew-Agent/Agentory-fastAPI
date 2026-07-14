"""관리자 라인·담당 라인 레포지토리 (BE_ADMIN01_LINE01)

라인은 hard delete (연결은 FK CASCADE로 정리), 담당 라인은 교체 방식으로 갱신
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.admin.models import EquipmentRepair, Line, UserLine
from agentory.modules.auth.models import User
from agentory.modules.telemetry.models import EquipmentMaster, EquipmentTelemetry


def _line_to_dict(row: Line) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "display_order": row.display_order,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _line_ref(row: Line) -> dict[str, Any]:
    # 유저 응답용 담당 라인 요약
    return {"id": row.id, "code": row.code, "name": row.name}


async def create_line(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    display_order: int | None,
) -> dict[str, Any]:
    # 라인 1건 생성
    row = Line(code=code, name=name, description=description, display_order=display_order)
    session.add(row)
    await session.flush()
    return _line_to_dict(row)


async def list_lines(session: AsyncSession, *, include_inactive: bool) -> list[dict[str, Any]]:
    # 정렬 순서 우선, 없으면 코드순, 기본은 활성 라인만
    stmt = select(Line)
    if not include_inactive:
        stmt = stmt.where(Line.status == "active")
    stmt = stmt.order_by(Line.display_order.asc().nulls_last(), Line.code.asc())
    rows = await session.scalars(stmt)
    return [_line_to_dict(r) for r in rows]


async def get_line(session: AsyncSession, line_id: int) -> dict[str, Any] | None:
    row = await session.get(Line, line_id)
    return _line_to_dict(row) if row else None


async def get_line_by_code(session: AsyncSession, code: str) -> dict[str, Any] | None:
    # code 고유성 사전 검사용
    row = await session.scalar(select(Line).where(Line.code == code))
    return _line_to_dict(row) if row else None


async def update_line(
    session: AsyncSession, line_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    row = await session.get(Line, line_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    # server_default는 최초 삽입만 반영, 수정 시각은 파이썬 값으로 명시 갱신
    # (SQL func 할당 시 flush 후 재조회가 필요해 async refresh 오류 발생)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return _line_to_dict(row)


async def delete_line(session: AsyncSession, line_id: int) -> bool:
    # 존재하면 삭제 후 True, 담당 라인 연결은 FK CASCADE로 함께 제거
    row = await session.get(Line, line_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def get_user(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    row = await session.get(User, user_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name,
        "role": row.role,
        "status": row.status,
    }


async def list_users(session: AsyncSession) -> list[dict[str, Any]]:
    # 유저 목록 (담당 라인은 서비스에서 합류)
    stmt = select(User).order_by(User.name.asc(), User.id.asc())
    rows = await session.scalars(stmt)
    return [
        {"id": r.id, "email": r.email, "name": r.name, "role": r.role, "status": r.status}
        for r in rows
    ]


async def list_user_line_refs(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    # 유저 1명의 담당 라인 요약 목록, 정렬 순서 우선
    stmt = (
        select(Line)
        .join(UserLine, UserLine.line_id == Line.id)
        .where(UserLine.user_id == user_id)
        .order_by(Line.display_order.asc().nulls_last(), Line.code.asc())
    )
    rows = await session.scalars(stmt)
    return [_line_ref(r) for r in rows]


async def line_refs_by_user(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    # 여러 유저의 담당 라인을 한 번에 조회해 user_id별로 묶기 (N+1 방지)
    if not user_ids:
        return {}
    stmt = (
        select(UserLine.user_id, Line)
        .join(Line, UserLine.line_id == Line.id)
        .where(UserLine.user_id.in_(user_ids))
        .order_by(Line.display_order.asc().nulls_last(), Line.code.asc())
    )
    result: dict[int, list[dict[str, Any]]] = {uid: [] for uid in user_ids}
    for user_id, line in await session.execute(stmt):
        result.setdefault(user_id, []).append(_line_ref(line))
    return result


async def existing_line_ids(session: AsyncSession, line_ids: list[int]) -> set[int]:
    # 넘어온 line_ids 중 실제 존재하는 것만 반환 (유효성 검사용)
    if not line_ids:
        return set()
    rows = await session.scalars(select(Line.id).where(Line.id.in_(line_ids)))
    return set(rows)


async def replace_user_lines(session: AsyncSession, user_id: int, line_ids: list[int]) -> None:
    # 기존 담당 라인 전체 삭제 후 새 집합으로 교체
    await session.execute(delete(UserLine).where(UserLine.user_id == user_id))
    for line_id in dict.fromkeys(line_ids):  # 중복 제거, 순서 유지
        session.add(UserLine(user_id=user_id, line_id=line_id))
    await session.flush()


async def set_equipment_manager(
    session: AsyncSession, equipment_id: str, manager_user_id: int | None
) -> bool:
    # 설비 책임자 유저 지정(또는 None으로 해제), 설비 미존재면 False
    equip = await session.get(EquipmentMaster, equipment_id)
    if equip is None:
        return False
    equip.manager_user_id = manager_user_id
    await session.flush()
    return True


def _repair_to_dict(row: EquipmentRepair, repaired_by_name: str | None) -> dict[str, Any]:
    # 수리 이력 행을 응답 공용 dict로 변환
    return {
        "id": row.id,
        "equipment_id": row.equipment_id,
        "repaired_by": row.repaired_by,
        "repaired_by_name": repaired_by_name,
        "repaired_at": row.repaired_at,
        "alarm_code_before": row.alarm_code_before,
        "note": row.note,
    }


async def create_repair(
    session: AsyncSession,
    *,
    equipment_id: str,
    repaired_by: int | None,
    note: str | None,
) -> dict[str, Any] | None:
    # 설비 수리 처리(NEW_REPAIR01_REPAIR01), 설비 미존재면 None
    # 직전 알람 스냅샷 후 이력 적재 + 마스터 수리 래치·점검일·알람 해제 시각 동시 갱신
    # commit은 서비스 계층 담당 (get_session 자동 커밋 없음)
    equip = await session.get(EquipmentMaster, equipment_id)
    if equip is None:
        return None
    # 수리 직전 최신 tick 알람 코드 스냅샷 (없으면 None)
    alarm_before = await session.scalar(
        select(EquipmentTelemetry.alarm_code)
        .where(EquipmentTelemetry.equipment_id == equipment_id)
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(1)
    )
    now = datetime.now(UTC)
    repair = EquipmentRepair(
        equipment_id=equipment_id,
        repaired_by=repaired_by,
        repaired_at=now,
        alarm_code_before=alarm_before,
        note=note,
    )
    session.add(repair)
    # 시뮬레이터 힐 윈도우 래치 + 점검일 + 알람 래치 해제 시각 동시 반영
    equip.repaired_at = now
    equip.last_inspection_at = now.date()
    equip.alarm_cleared_at = now
    await session.flush()
    name = await session.scalar(select(User.name).where(User.id == repaired_by))
    return _repair_to_dict(repair, name)


async def fetch_repairs_page(
    session: AsyncSession,
    *,
    equipment_id: str | None = None,
    repaired_by: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    before: tuple[datetime, int] | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    # 수리 이력 목록(NEW_REPAIR01_HISTORY01), 수리 역순 (repaired_at, id) 키셋 페이지네이션
    # 책임자 이름 표기 위해 users 좌외부조인, 유저 삭제(SET NULL) 이력도 노출
    stmt = select(EquipmentRepair, User.name).outerjoin(
        User, User.id == EquipmentRepair.repaired_by
    )
    if equipment_id:
        stmt = stmt.where(EquipmentRepair.equipment_id == equipment_id)
    if repaired_by is not None:
        stmt = stmt.where(EquipmentRepair.repaired_by == repaired_by)
    if start is not None:
        stmt = stmt.where(EquipmentRepair.repaired_at >= start)
    if end is not None:
        stmt = stmt.where(EquipmentRepair.repaired_at <= end)
    if before is not None:
        # row-value 튜플 비교로 커서, OR 펼침 대비 sargable 해 인덱스 커서 위치로 직접 seek
        # 전역 페이지는 ix_equipment_repairs_repaired_at_id 활용
        stmt = stmt.where(tuple_(EquipmentRepair.repaired_at, EquipmentRepair.id) < before)
    stmt = stmt.order_by(EquipmentRepair.repaired_at.desc(), EquipmentRepair.id.desc()).limit(limit)
    rows = await session.execute(stmt)
    return [_repair_to_dict(repair, name) for repair, name in rows]


async def equipment_exists(session: AsyncSession, equipment_id: str) -> bool:
    # 설비 존재 확인 (장비별 이력 조회 404 판정용)
    return (
        await session.scalar(
            select(func.count())
            .select_from(EquipmentMaster)
            .where(EquipmentMaster.equipment_id == equipment_id)
        )
        > 0
    )
