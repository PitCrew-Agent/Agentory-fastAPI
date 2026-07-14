"""텔레메트리 조회 레포지토리

MCP realtime 서버와 트윈용 REST가 공유하는 조회 로직
JSON 직렬화 가능한 dict를 반환하므로 MCP 도구가 그대로 노출 가능
(BE_MCP02_TELEMETRY01 / BE_MCP02_TELEMETRY02 / BE_MCP03_MASTER01)
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select, true, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agentory.core.config import get_settings
from agentory.modules.auth.models import User
from agentory.modules.telemetry.models import (
    EquipmentAlarm,
    EquipmentMaster,
    EquipmentTelemetry,
)


def _num(value: Decimal | None) -> float | None:
    # Decimal을 JSON 친화적인 float로 변환, None은 유지
    return float(value) if value is not None else None


def _telemetry_to_dict(row: EquipmentTelemetry) -> dict[str, Any]:
    # 텔레메트리 행 하나를 JSON 직렬화용 dict로 변환
    return {
        "equipment_id": row.equipment_id,
        "timestamp": row.timestamp.isoformat(),
        "temperature": _num(row.temperature),
        "pressure": _num(row.pressure),
        "rf_power": _num(row.rf_power),
        "gas_flow": _num(row.gas_flow),
        "alarm_code": row.alarm_code,
    }


async def fetch_sensor_logs(
    session: AsyncSession,
    *,
    start_time: datetime,
    end_time: datetime,
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    # 센서 로그 조회 (BE_MCP02_TELEMETRY01)
    # 지정 기간 필터 + 시간순 정렬
    # 행수 상한(BE_MCP02_TELEMETRY01), 대용량 구간 조회가 LLM 컨텍스트를 넘기지 않도록
    # 최근 행 우선으로 LIMIT 후 시간 오름차순으로 되돌려 반환
    limit = get_settings().sensor_log_max_rows
    if line_name and not equipment_id:
        # 라인 소속 설비별 top-N을 LATERAL로 뽑아 병합, 라인 전체 스캔·정렬 회피
        # 설비마다 (equipment_id, timestamp) 인덱스 후방 스캔 limit건 후 병합해 최종 limit건
        per_equipment = (
            select(EquipmentTelemetry)
            .where(
                EquipmentTelemetry.equipment_id == EquipmentMaster.equipment_id,
                EquipmentTelemetry.timestamp >= start_time,
                EquipmentTelemetry.timestamp <= end_time,
            )
            .order_by(EquipmentTelemetry.timestamp.desc())
            .limit(limit)
            .lateral()
        )
        telem = aliased(EquipmentTelemetry, per_equipment)
        stmt = (
            select(telem)
            .select_from(EquipmentMaster)
            .join(telem, true())
            .where(EquipmentMaster.line_name == line_name)
            .order_by(telem.timestamp.desc())
            .limit(limit)
        )
    else:
        stmt = select(EquipmentTelemetry).where(
            EquipmentTelemetry.timestamp >= start_time,
            EquipmentTelemetry.timestamp <= end_time,
        )
        if equipment_id:
            # 단일 설비로 좁힘, (equipment_id, timestamp) 인덱스 후방 스캔
            stmt = stmt.where(EquipmentTelemetry.equipment_id == equipment_id)
        stmt = stmt.order_by(EquipmentTelemetry.timestamp.desc()).limit(limit)

    rows = list(await session.scalars(stmt))
    rows.reverse()
    # 결과 없으면 빈 목록 반환
    return [_telemetry_to_dict(r) for r in rows]


async def fetch_alarm_history(
    session: AsyncSession,
    *,
    equipment_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    alarm_code: str | None = None,
) -> list[dict[str, Any]]:
    # 알람 이력 집계 (BE_MCP02_TELEMETRY02 / NEW_ALARM01_HISTORY02)
    # 알람 코드별 발생 횟수·최초/최근 시각 집계, 다발 순 정렬 (NULL 알람 제외)
    # 기간(start/end) 미지정 시 전체 이력 대상 (REST 요약 조회는 기본 전체)
    stmt = (
        select(
            EquipmentTelemetry.alarm_code,
            func.count().label("count"),
            func.min(EquipmentTelemetry.timestamp).label("first_seen"),
            func.max(EquipmentTelemetry.timestamp).label("last_seen"),
        )
        .where(
            EquipmentTelemetry.equipment_id == equipment_id,
            EquipmentTelemetry.alarm_code.is_not(None),
        )
        .group_by(EquipmentTelemetry.alarm_code)
        .order_by(func.count().desc())
    )
    if start_time is not None:
        stmt = stmt.where(EquipmentTelemetry.timestamp >= start_time)
    if end_time is not None:
        stmt = stmt.where(EquipmentTelemetry.timestamp <= end_time)
    if alarm_code:
        # 특정 알람 코드로 좁힘
        stmt = stmt.where(EquipmentTelemetry.alarm_code == alarm_code)

    rows = await session.execute(stmt)
    # 코드별 집계 행을 dict로 변환
    return [
        {
            "alarm_code": code,
            "count": count,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
        }
        for code, count, first_seen, last_seen in rows
    ]


async def fetch_alarm_events(
    session: AsyncSession,
    *,
    equipment_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    alarm_code: str | None = None,
    before: tuple[datetime, int] | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    # 장비별 알람 발생 이벤트 타임라인 (NEW_ALARM01_HISTORY01)
    # 원본 텔레메트리에서 alarm_code 있는 tick만 발생 역순 (timestamp, log_id) 키셋 페이지네이션
    # 새 알람이 위에 쌓여도 경계가 밀리지 않도록 offset 대신 키셋 사용
    stmt = select(EquipmentTelemetry).where(
        EquipmentTelemetry.equipment_id == equipment_id,
        EquipmentTelemetry.alarm_code.is_not(None),
    )
    if start_time is not None:
        stmt = stmt.where(EquipmentTelemetry.timestamp >= start_time)
    if end_time is not None:
        stmt = stmt.where(EquipmentTelemetry.timestamp <= end_time)
    if alarm_code:
        # 특정 알람 코드로 좁힘
        stmt = stmt.where(EquipmentTelemetry.alarm_code == alarm_code)
    if before is not None:
        # row-value 튜플 비교로 커서, OR 펼침 대비 sargable 해 인덱스 커서 위치로 직접 seek
        stmt = stmt.where(tuple_(EquipmentTelemetry.timestamp, EquipmentTelemetry.log_id) < before)
    stmt = stmt.order_by(
        EquipmentTelemetry.timestamp.desc(), EquipmentTelemetry.log_id.desc()
    ).limit(limit)
    rows = await session.scalars(stmt)
    return [
        {"occurred_at": r.timestamp, "alarm_code": r.alarm_code, "log_id": r.log_id} for r in rows
    ]


async def fetch_latest_telemetry(session: AsyncSession, equipment_id: str) -> dict[str, Any] | None:
    # 설비 최신 텔레메트리 1건, 없으면 None (NEW_LOOP01_CHECK01 상태 판정용)
    stmt = (
        select(EquipmentTelemetry)
        .where(EquipmentTelemetry.equipment_id == equipment_id)
        .order_by(EquipmentTelemetry.timestamp.desc())
        .limit(1)
    )
    row = await session.scalar(stmt)
    return _telemetry_to_dict(row) if row else None


def _severity_rank(alarm_code):
    # 알람 심각도 순위 (ERR 위험=2 > WRN 주의=1), 래치 시 최고 심각도 채택용
    return case((alarm_code.like("ERR%"), 2), else_=1)


def _latched_where(t, m):
    # 확정 알람 중 해제 시각(alarm_cleared_at) 이후만 유효, NULL 해제는 전체 이력 반영
    return (
        t.alarm_code.is_not(None),
        or_(m.alarm_cleared_at.is_(None), t.timestamp > m.alarm_cleared_at),
    )


async def fetch_latched_alarm(session: AsyncSession, equipment_id: str) -> str | None:
    # 단일 설비의 래치된 알람 코드 (NEW_LOOP01_LATCH01)
    # 해제 시각 이후 확정 알람 중 최고 심각도·최신 코드, 없으면 None(양호)
    # 상태 판정은 실시간 최신 tick 기준으로 전환, 래치 조회는 스캐폴딩으로만 유지
    m = EquipmentMaster
    t = EquipmentTelemetry
    stmt = (
        select(t.alarm_code)
        .join(m, m.equipment_id == t.equipment_id)
        .where(t.equipment_id == equipment_id, *_latched_where(t, m))
        .order_by(_severity_rank(t.alarm_code).desc(), t.timestamp.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def clear_equipment_alarm(session: AsyncSession, equipment_id: str) -> bool:
    # 알람 래치 해제(현장 점검·수리 반영), 해제 시각·점검일 갱신, 대상 없으면 False
    # commit은 서비스 계층 담당 (get_session 자동 커밋 없음)
    stmt = (
        update(EquipmentMaster)
        .where(EquipmentMaster.equipment_id == equipment_id)
        .values(alarm_cleared_at=func.now(), last_inspection_at=func.current_date())
    )
    result = await session.execute(stmt)
    # 수동 해제 시 변수별 열린 알람도 함께 종료해 저널 정합 유지
    await session.execute(
        update(EquipmentAlarm)
        .where(
            EquipmentAlarm.equipment_id == equipment_id,
            EquipmentAlarm.cleared_at.is_(None),
        )
        .values(cleared_at=func.now())
    )
    return result.rowcount > 0


async def fetch_alarm_sensor_summary(
    session: AsyncSession,
    *,
    equipment_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    # 센서 변수(metric)별 알람 발생 횟수·최초/최근 발생 시각 집계 (NEW_ALARM01_HISTORY02)
    # 도넛 차트용 센서별 집계, 기간(start/end) 미지정 시 전체 이력 대상
    stmt = (
        select(
            EquipmentAlarm.metric,
            func.count().label("count"),
            func.min(EquipmentAlarm.raised_at).label("first_seen"),
            func.max(EquipmentAlarm.raised_at).label("last_seen"),
        )
        .where(EquipmentAlarm.equipment_id == equipment_id)
        .group_by(EquipmentAlarm.metric)
        .order_by(func.count().desc())
    )
    if start is not None:
        stmt = stmt.where(EquipmentAlarm.raised_at >= start)
    if end is not None:
        stmt = stmt.where(EquipmentAlarm.raised_at <= end)

    rows = await session.execute(stmt)
    return [
        {
            "metric": metric,
            "count": count,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
        }
        for metric, count, first_seen, last_seen in rows
    ]


async def fetch_latest_status_rows(
    session: AsyncSession, *, line_name: str | None = None
) -> list[dict[str, Any]]:
    # 전체 설비의 현재 상태 (NEW_TWIN01_SYNC01), 텔레메트리 없는 설비도 포함
    # 3D 뷰는 실시간 최신 tick의 alarm_code를 그대로 반영, 래치(sticky)는 판정에서 제외
    # 설비별 최신 tick 한 건을 LATERAL로 추림, 마스터 각 행마다 (equipment_id, timestamp)
    # 인덱스 역방향 1건만 읽어 DISTINCT ON 전체 정렬(대용량 디스크 머지) 회피
    # 행수 증가에도 상수 시간, 최신이 정상이면 alarm_code NULL(양호)
    m = EquipmentMaster
    t = EquipmentTelemetry
    latest = (
        select(t.alarm_code)
        .where(t.equipment_id == m.equipment_id)
        .order_by(t.timestamp.desc())
        .limit(1)
        .lateral()
    )
    # 마스터 기준 좌외부조인, 텔레메트리 없는 설비는 alarm_code NULL(양호)
    # 3D 배치값(위치·회전·shape 등)을 함께 반환해 프론트가 상태 색상과 배치를 한 번에 렌더
    stmt = (
        select(
            m.equipment_id,
            m.line_name,
            latest.c.alarm_code,
            m.display_order,
            m.shape,
            m.bay_zone,
            m.position_x,
            m.position_y,
            m.position_z,
            m.rotation_y,
        )
        .outerjoin(latest, true())
        .order_by(m.line_name, m.display_order, m.equipment_id)
    )
    if line_name:
        # 특정 라인 소속으로 좁힘
        stmt = stmt.where(m.line_name == line_name)
    rows = await session.execute(stmt)
    return [
        {
            "equipment_id": row.equipment_id,
            "line_name": row.line_name,
            "alarm_code": row.alarm_code,
            "display_order": row.display_order,
            "shape": row.shape,
            "bay_zone": row.bay_zone,
            "position_x": _num(row.position_x),
            "position_y": _num(row.position_y),
            "position_z": _num(row.position_z),
            "rotation_y": _num(row.rotation_y),
        }
        for row in rows
    ]


async def fetch_lines(session: AsyncSession) -> list[dict[str, Any]]:
    # 라인 목록 + 라인별 설비 수 (라인 선택 드롭다운·3D 뷰 전환용), 라인명순 정렬
    stmt = (
        select(EquipmentMaster.line_name, func.count(EquipmentMaster.equipment_id))
        .group_by(EquipmentMaster.line_name)
        .order_by(EquipmentMaster.line_name)
    )
    rows = await session.execute(stmt)
    return [{"line_name": name, "equipment_count": count} for name, count in rows]


async def fetch_equipment_metadata(
    session: AsyncSession,
    *,
    equipment_id: str | None = None,
    line_name: str | None = None,
) -> list[dict[str, Any]]:
    # 설비 메타데이터 조회 (BE_MCP03_MASTER01)
    stmt = select(EquipmentMaster)
    if equipment_id:
        # 특정 설비 한 건
        stmt = stmt.where(EquipmentMaster.equipment_id == equipment_id)
    elif line_name:
        # 특정 라인 소속 전체
        stmt = stmt.where(EquipmentMaster.line_name == line_name)

    rows = await session.scalars(stmt)
    # 존재하지 않으면 빈 목록 반환
    return [
        {
            "equipment_id": e.equipment_id,
            "line_name": e.line_name,
            "process_type": e.process_type,
            "location": e.location,
            "manager_dept": e.manager_dept,
            "manager_name": e.manager_name,
            "manager_user_id": e.manager_user_id,
            "last_inspection_at": (
                e.last_inspection_at.isoformat() if e.last_inspection_at else None
            ),
        }
        for e in rows
    ]


async def fetch_user_ref(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    # 책임자 유저 요약 조회 (BE_ADMIN01_MANAGER01), 없으면 None
    user = await session.get(User, user_id)
    if user is None:
        return None
    return {"id": user.id, "name": user.name, "email": user.email}
