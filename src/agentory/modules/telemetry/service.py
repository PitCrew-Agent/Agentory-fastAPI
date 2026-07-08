"""텔레메트리 상태 판정·체크리스트 서비스 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)

최신 텔레메트리 alarm_code로 설비 상태를 판정하고, 경고·이상이면 조치 체크리스트를 구성
상태 판정 규칙은 전체 상태 목록과 선택 설비 상세가 공유
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.modules.telemetry import repository
from agentory.modules.telemetry.checklists import build_checklist_items
from agentory.modules.telemetry.schemas import (
    ChecklistItem,
    EquipmentDetail,
    EquipmentStatusItem,
    LineItem,
    ScenePosition,
    SensorPoint,
    StatusLevel,
)

# 시계열 기간 미지정 시 최신 텔레메트리 기준 기본 조회 폭
DEFAULT_SERIES_WINDOW = timedelta(hours=6)


def assess_status(alarm_code: str | None) -> StatusLevel:
    # 알람 코드 접두로 상태 판정, 미분류 알람은 보수적으로 경고 처리
    if not alarm_code:
        return StatusLevel.NORMAL
    if alarm_code.startswith("ERR"):
        return StatusLevel.CRITICAL
    return StatusLevel.WARNING


async def list_equipment_status(
    session: AsyncSession, line_name: str | None = None
) -> list[EquipmentStatusItem]:
    # 설비 최신 상태 목록, 텔레메트리 없는 설비는 양호, line_name 지정 시 해당 라인만
    rows = await repository.fetch_latest_status_rows(session, line_name=line_name)
    return [
        EquipmentStatusItem(
            equipment_id=row["equipment_id"],
            line_name=row["line_name"],
            status=assess_status(row["alarm_code"]),
            alarm_code=row["alarm_code"],
            display_order=row["display_order"],
            shape=row["shape"],
            bay_zone=row["bay_zone"],
            position=_scene_position(row),
            rotation_y=row["rotation_y"],
        )
        for row in rows
    ]


def _scene_position(row: dict) -> ScenePosition | None:
    # 배치 좌표가 하나라도 있으면 3D 위치 구성, 미설정 설비는 None
    if row["position_x"] is None and row["position_y"] is None and row["position_z"] is None:
        return None
    return ScenePosition(
        x=row["position_x"] or 0.0,
        y=row["position_y"] or 0.0,
        z=row["position_z"] or 0.0,
    )


async def list_lines(session: AsyncSession) -> list[LineItem]:
    # 라인 목록 + 라인별 설비 수 (라인 선택 드롭다운용)
    rows = await repository.fetch_lines(session)
    return [
        LineItem(line_name=row["line_name"], equipment_count=row["equipment_count"]) for row in rows
    ]


async def get_sensor_series(
    session: AsyncSession,
    equipment_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[SensorPoint] | None:
    # 설비 존재 확인, 미존재면 None(라우터에서 404)
    if not await repository.fetch_equipment_metadata(session, equipment_id=equipment_id):
        return None
    # 기간 미지정 시 최신 텔레메트리 기준 최근 window로 기본값 (그래프 초기 표시용)
    if start is None or end is None:
        latest = await repository.fetch_latest_telemetry(session, equipment_id)
        if latest is None:
            # 텔레메트리 없는 설비는 빈 시계열
            return []
        anchor = datetime.fromisoformat(latest["timestamp"])
        end = end or anchor
        start = start or (end - DEFAULT_SERIES_WINDOW)
    logs = await repository.fetch_sensor_logs(
        session, start_time=start, end_time=end, equipment_id=equipment_id
    )
    return [
        SensorPoint(
            timestamp=log["timestamp"],
            temperature=log["temperature"],
            pressure=log["pressure"],
            rf_power=log["rf_power"],
            gas_flow=log["gas_flow"],
        )
        for log in logs
    ]


async def get_equipment_detail(session: AsyncSession, equipment_id: str) -> EquipmentDetail | None:
    # 설비 메타 조회 겸 존재 확인, 미존재면 None(라우터에서 404)
    metas = await repository.fetch_equipment_metadata(session, equipment_id=equipment_id)
    if not metas:
        return None
    meta = metas[0]
    # 최신 텔레메트리 1건으로 상태·센서·체크리스트 구성
    latest = await repository.fetch_latest_telemetry(session, equipment_id)
    alarm_code = latest["alarm_code"] if latest else None
    checklist = [ChecklistItem(text=t) for t in build_checklist_items(alarm_code)]
    return EquipmentDetail(
        equipment_id=equipment_id,
        status=assess_status(alarm_code),
        alarm_code=alarm_code,
        process_type=meta["process_type"],
        manager_name=meta["manager_name"],
        last_inspection_at=meta["last_inspection_at"],
        updated_at=latest["timestamp"] if latest else None,
        temperature=latest["temperature"] if latest else None,
        pressure=latest["pressure"] if latest else None,
        rf_power=latest["rf_power"] if latest else None,
        gas_flow=latest["gas_flow"] if latest else None,
        checklist=checklist,
    )
