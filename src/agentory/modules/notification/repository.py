"""알림 조회·동기화 레포지토리 (NEW_PROACT01_ALERT01)

변수별 알람 저널(EquipmentAlarm)을 notifications로 멱등 동기화(백그라운드 워처 호출), 읽음 상태 갱신
동일 설비+변수+알람은 30분 버킷당 첫 알람 1건만 적재해 중복 억제 (NEW_PROACT01_ALERT03)
"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, false, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.admin.models import Line, UserLine
from agentory.modules.notification.messages import build_notification_message
from agentory.modules.notification.models import Notification, NotificationRead
from agentory.modules.telemetry.models import EquipmentAlarm, EquipmentMaster
from agentory.modules.telemetry.schemas import StatusLevel, alarm_severity

# 30분 버킷 경계 기준점(00분·30분 정렬용), date_bin origin으로 사용
_BUCKET_ORIGIN = text("timestamptz '2000-01-01 00:00:00+00'")
_BUCKET_WIDTH = text("interval '30 minutes'")


def _severity(alarm_code: str) -> StatusLevel:
    # 심각도는 telemetry.alarm_severity 단일 소스에 위임 (복합 냉각 ERR-402만 위험·그 외 주의)
    # 프론트가 코드로 재추론하지 않도록 서버가 명시적으로 딱지 부여
    return StatusLevel(alarm_severity(alarm_code))


def _to_dict(row: Notification, *, is_read: bool = False) -> dict[str, Any]:
    # 알림 행을 응답·이벤트 공용 dict로 변환
    # message는 저장값 대신 코드에서 요청 로케일로 재빌드해 다국어 응답 (ko는 저장값과 동일)
    # is_read는 알림 자체 속성이 아니라 조회 사용자 기준 판정값 (BE_NOTI01_SCOPE01)
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "equipment_id": row.equipment_id,
        "line_name": row.line_name,
        "metric": row.metric,
        "alarm_code": row.alarm_code,
        "severity": _severity(row.alarm_code),
        "message": build_notification_message(row.equipment_id, row.alarm_code),
        "is_read": is_read,
    }


def _read_exists(user_id: int | None):
    # 조회 사용자의 읽음 행 존재 조건, user_id 없으면 항상 미읽음 취급
    if user_id is None:
        return false()
    return (
        select(NotificationRead.id)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == user_id,
        )
        .exists()
    )


async def sync_from_alarms(session: AsyncSession, since: datetime | None = None) -> int:
    # 설비+변수+알람+30분버킷별 첫 알람만 알림화, 버킷당 1건 유니크로 멱등, 신규 적재 건수 반환
    # since 지정 시 최근 창만 집계해 풀스캔 방지, 과거 버킷은 멱등 적재됨 (NEW_PROACT01_DETECT01)
    bucket = func.date_bin(_BUCKET_WIDTH, EquipmentAlarm.raised_at, _BUCKET_ORIGIN)
    # 담당 라인 스코핑 기준값을 적재 시점에 해석, 마스터 미등록 설비는 outer join으로 NULL 유지
    grouped = select(
        EquipmentAlarm.equipment_id.label("equipment_id"),
        EquipmentMaster.line_name.label("line_name"),
        EquipmentAlarm.metric.label("metric"),
        EquipmentAlarm.alarm_code.label("alarm_code"),
        bucket.label("bucket_start"),
        func.min(EquipmentAlarm.raised_at).label("occurred_at"),
        func.min(EquipmentAlarm.alarm_id).label("source_alarm_id"),
    ).outerjoin(EquipmentMaster, EquipmentMaster.equipment_id == EquipmentAlarm.equipment_id)
    if since is not None:
        grouped = grouped.where(EquipmentAlarm.raised_at >= since)
    grouped = grouped.group_by(
        EquipmentAlarm.equipment_id,
        EquipmentMaster.line_name,
        EquipmentAlarm.metric,
        EquipmentAlarm.alarm_code,
        bucket,
    )
    groups = (await session.execute(grouped)).all()
    if not groups:
        return 0
    values = [
        {
            "occurred_at": g.occurred_at,
            "equipment_id": g.equipment_id,
            "line_name": g.line_name,
            "metric": g.metric,
            "alarm_code": g.alarm_code,
            "bucket_start": g.bucket_start,
            "message": build_notification_message(g.equipment_id, g.alarm_code),
            "source_alarm_id": g.source_alarm_id,
        }
        for g in groups
    ]
    # 기존 버킷은 유니크 충돌로 무시, 신규 버킷만 적재
    stmt = (
        pg_insert(Notification)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_notifications_equip_metric_alarm_bucket")
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount or 0


async def assigned_line_names(session: AsyncSession, user_id: int) -> list[str]:
    # 사용자 담당 라인 코드 목록, EquipmentMaster.line_name과 매칭되는 값 (BE_NOTI01_SCOPE01)
    stmt = (
        select(Line.code)
        .join(UserLine, UserLine.line_id == Line.id)
        .where(UserLine.user_id == user_id)
    )
    return list(await session.scalars(stmt))


def _scope_to_lines(stmt, line_names: list[str] | None):
    # 담당 라인 조건 적용, None이면 전체 허용(관리자), 빈 목록이면 결과 없음(라인 미배정)
    if line_names is None:
        return stmt
    if not line_names:
        return stmt.where(false())
    return stmt.where(Notification.line_name.in_(line_names))


def _scope_to_range(stmt, start: datetime | None, end: datetime | None):
    # 발생 시각 반열림 구간, start 포함·end 미포함으로 인접 구간 경계 중복 방지 (BE_NOTI01_RANGE01)
    # 경계는 프론트가 tz 포함해 산출, 서버는 timestamptz 비교만 수행해 tz 가정 배제
    if start is not None:
        stmt = stmt.where(Notification.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(Notification.occurred_at < end)
    return stmt


async def fetch_notifications(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    after_id: int | None = None,
    line_names: list[str] | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    # after_id 지정 시 그보다 큰 id 오름차순(SSE 증분용), 아니면 발생 역순(이력용)
    read_flag = _read_exists(user_id)
    stmt = _scope_to_lines(select(Notification, read_flag.label("is_read")), line_names)
    if unread_only:
        stmt = stmt.where(~read_flag)
    if after_id is not None:
        stmt = stmt.where(Notification.id > after_id).order_by(Notification.id)
    else:
        stmt = stmt.order_by(Notification.occurred_at.desc(), Notification.id.desc())
    rows = await session.execute(stmt)
    return [_to_dict(row, is_read=is_read) for row, is_read in rows]


async def fetch_notifications_page(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    offset: int = 0,
    limit: int,
    line_names: list[str] | None = None,
    user_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    # 발생 역순(occurred_at, id) offset 페이지네이션
    # 화면이 페이지 번호로 임의 이동하므로 커서 대신 offset 사용, 정렬키는 인덱스 그대로 활용
    read_flag = _read_exists(user_id)
    stmt = _scope_to_lines(select(Notification, read_flag.label("is_read")), line_names)
    stmt = _scope_to_range(stmt, start, end)
    if unread_only:
        stmt = stmt.where(~read_flag)
    stmt = (
        stmt.order_by(Notification.occurred_at.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return [_to_dict(row, is_read=is_read) for row, is_read in rows]


async def count_notifications(
    session: AsyncSession,
    *,
    unread_only: bool = False,
    line_names: list[str] | None = None,
    user_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> int:
    # 조회 조건에 해당하는 전체 건수, 화면의 총 건수·페이지 수 표기용
    # 페이지 조회와 동일 필터를 적용해야 페이지네이션 총계가 정합 유지 (BE_NOTI01_RANGE01)
    read_flag = _read_exists(user_id)
    stmt = _scope_to_lines(select(func.count()).select_from(Notification), line_names)
    stmt = _scope_to_range(stmt, start, end)
    if unread_only:
        stmt = stmt.where(~read_flag)
    return int(await session.scalar(stmt) or 0)


async def mark_read(
    session: AsyncSession,
    notification_id: int,
    user_id: int,
    *,
    line_names: list[str] | None = None,
) -> bool:
    # 개별 읽음, 담당 라인 밖이거나 대상 없으면 False
    target = _scope_to_lines(
        select(Notification.id).where(Notification.id == notification_id), line_names
    )
    if await session.scalar(target) is None:
        return False
    # 이미 읽은 알림은 유니크 충돌로 무시, 재요청도 성공 취급
    stmt = (
        pg_insert(NotificationRead)
        .values(notification_id=notification_id, user_id=user_id)
        .on_conflict_do_nothing(constraint="uq_notification_reads_notification_user")
    )
    await session.execute(stmt)
    return True


async def mark_unread(
    session: AsyncSession,
    notification_id: int,
    user_id: int,
    *,
    line_names: list[str] | None = None,
) -> bool:
    # 읽음 해제, 담당 라인 밖이거나 대상 없으면 False
    target = _scope_to_lines(
        select(Notification.id).where(Notification.id == notification_id), line_names
    )
    if await session.scalar(target) is None:
        return False
    await session.execute(
        delete(NotificationRead).where(
            NotificationRead.notification_id == notification_id,
            NotificationRead.user_id == user_id,
        )
    )
    return True


async def mark_all_read(
    session: AsyncSession, user_id: int, *, line_names: list[str] | None = None
) -> int:
    # 담당 라인 미읽음 전체 읽음 처리, 신규 읽음 건수 반환
    unread = _scope_to_lines(select(Notification.id), line_names).where(~_read_exists(user_id))
    ids = list(await session.scalars(unread))
    if not ids:
        return 0
    stmt = (
        pg_insert(NotificationRead)
        .values([{"notification_id": nid, "user_id": user_id} for nid in ids])
        .on_conflict_do_nothing(constraint="uq_notification_reads_notification_user")
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
