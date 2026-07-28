"""텔레메트리 상태 판정·체크리스트 서비스 (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)

최신 텔레메트리 alarm_code로 설비 상태를 판정하고, 경고·이상이면 조치 체크리스트를 구성
상태 판정 규칙은 전체 상태 목록과 선택 설비 상세가 공유
"""

import base64
import binascii
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import ValidationError
from agentory.modules.agent.equipment_suggest import generate_equipment_suggestions
from agentory.modules.telemetry import repository
from agentory.modules.telemetry.checklists import alarm_metrics, build_checklist_items
from agentory.modules.telemetry.schemas import (
    CRITICAL_ALARM_CODES,
    AlarmEventItem,
    AlarmHistoryPage,
    AlarmSensorSummaryItem,
    AlarmSummaryItem,
    ChecklistItem,
    EquipmentDetail,
    EquipmentManager,
    EquipmentStatusItem,
    LineItem,
    ScenePosition,
    SensorPoint,
    StatusLevel,
)

# 시계열 기간 미지정 시 최신 텔레메트리 기준 기본 조회 폭
DEFAULT_SERIES_WINDOW = timedelta(hours=6)

# 장비별 알람 이력 페이지 크기 기본값·상한
DEFAULT_ALARM_PAGE_SIZE = 10
MAX_ALARM_PAGE_SIZE = 50


# 이상 감지 워처 발령 코드, 규칙이 침묵인 밴드 안 이상 설비의 상태 표면화용
ANOMALY_ALARM_CODE = "WRN-901"


def assess_status(alarm_code: str | None) -> StatusLevel:
    # 복합 냉각·다변량만 위험, 단일 밴드 이탈은 주의, 미분류는 보수적 경고 (매뉴얼 상태 판정)
    if not alarm_code:
        return StatusLevel.NORMAL
    if alarm_code in CRITICAL_ALARM_CODES:
        return StatusLevel.CRITICAL
    return StatusLevel.WARNING


async def list_equipment_status(
    session: AsyncSession, line_name: str | None = None
) -> list[EquipmentStatusItem]:
    # 설비 최신 상태 목록, 텔레메트리 없는 설비는 양호, line_name 지정 시 해당 라인만
    rows = await repository.fetch_latest_status_rows(session, line_name=line_name)
    # 규칙 무알람이나 활성 이상 감지(WRN-901)가 있으면 주의로 표면화 (규칙 알람 있으면 그대로 우선)
    anomaly_active = await repository.fetch_active_anomaly_equipment(session)
    items = []
    for row in rows:
        alarm_code = row["alarm_code"]
        if not alarm_code and row["equipment_id"] in anomaly_active:
            alarm_code = ANOMALY_ALARM_CODE
        items.append(
            EquipmentStatusItem(
                equipment_id=row["equipment_id"],
                line_name=row["line_name"],
                status=assess_status(alarm_code),
                alarm_code=alarm_code,
                display_order=row["display_order"],
                shape=row["shape"],
                bay_zone=row["bay_zone"],
                position=_scene_position(row),
                rotation_y=row["rotation_y"],
            )
        )
    return items


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
    # 센서값·상태·알람 모두 실시간 최신 tick 기준 (3D 뷰와 동일 소스로 일치, 래치는 판정 제외)
    latest = await repository.fetch_latest_telemetry(session, equipment_id)
    alarm_code = latest["alarm_code"] if latest else None
    # 규칙 무알람이나 활성 이상 감지(WRN-901) 보유 시 표면화 (3D 뷰 상태와 동일 판정)
    if not alarm_code and equipment_id in await repository.fetch_active_anomaly_equipment(session):
        alarm_code = ANOMALY_ALARM_CODE
    checklist = [ChecklistItem(text=t) for t in build_checklist_items(alarm_code)]
    # 책임자 유저 지정 시 요약 로드, 미지정이면 레거시 manager_name만 노출
    manager = None
    if meta.get("manager_user_id"):
        ref = await repository.fetch_user_ref(session, meta["manager_user_id"])
        manager = EquipmentManager(**ref) if ref else None
    return EquipmentDetail(
        equipment_id=equipment_id,
        status=assess_status(alarm_code),
        alarm_code=alarm_code,
        alarm_metrics=alarm_metrics(alarm_code),
        process_type=meta["process_type"],
        manager_name=meta["manager_name"],
        manager=manager,
        last_inspection_at=meta["last_inspection_at"],
        updated_at=latest["timestamp"] if latest else None,
        temperature=latest["temperature"] if latest else None,
        pressure=latest["pressure"] if latest else None,
        rf_power=latest["rf_power"] if latest else None,
        gas_flow=latest["gas_flow"] if latest else None,
        checklist=checklist,
    )


async def get_equipment_suggestions(session: AsyncSession, equipment_id: str) -> list[str] | None:
    # 선택 설비 상세(상태·알람·센서) 조회 겸 존재 확인, 미존재면 None(라우터 404)
    detail = await get_equipment_detail(session, equipment_id)
    if detail is None:
        return None
    # 현재 상태를 근거로 챗봇 추천 메시지 3개 생성, 생성 실패는 함수 내부에서 빈 목록으로 격리
    return await generate_equipment_suggestions(
        equipment_id=detail.equipment_id,
        status=detail.status.value,
        alarm_code=detail.alarm_code,
        alarm_metrics=detail.alarm_metrics,
        sensors={
            "temperature": detail.temperature,
            "pressure": detail.pressure,
            "rf_power": detail.rf_power,
            "gas_flow": detail.gas_flow,
        },
    )


def _encode_alarm_cursor(occurred_at: datetime, log_id: int) -> str:
    # 커서는 마지막 항목의 (발생시각, log_id)를 base64로 감싼 불투명 토큰
    raw = f"{occurred_at.isoformat()}|{log_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_alarm_cursor(cursor: str) -> tuple[datetime, int]:
    # 잘못된 커서는 ValueError (라우터에서 400)
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(occurred_str), int(id_str)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("error.cursor.invalid", params={"cursor": cursor}) from exc


async def list_alarm_events(
    session: AsyncSession,
    equipment_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    alarm_code: str | None = None,
    before: str | None = None,
    limit: int = DEFAULT_ALARM_PAGE_SIZE,
) -> AlarmHistoryPage | None:
    # 장비별 알람 발생 이벤트 타임라인 (NEW_ALARM01_HISTORY01)
    # 설비 존재 확인, 미존재면 None(라우터에서 404), 알람 없으면 빈 페이지
    if not await repository.fetch_equipment_metadata(session, equipment_id=equipment_id):
        return None
    cursor = _decode_alarm_cursor(before) if before else None
    page_size = max(1, min(limit, MAX_ALARM_PAGE_SIZE))
    # 다음 페이지 존재 여부 판단 위해 한 개 더 조회
    rows = await repository.fetch_alarm_events(
        session,
        equipment_id=equipment_id,
        start_time=start,
        end_time=end,
        alarm_code=alarm_code,
        before=cursor,
        limit=page_size + 1,
    )
    has_more = len(rows) > page_size
    page_rows = rows[:page_size]
    next_cursor = (
        _encode_alarm_cursor(page_rows[-1]["occurred_at"], page_rows[-1]["log_id"])
        if has_more and page_rows
        else None
    )
    return AlarmHistoryPage(
        items=[
            AlarmEventItem(
                occurred_at=row["occurred_at"],
                alarm_code=row["alarm_code"],
                severity=assess_status(row["alarm_code"]),
            )
            for row in page_rows
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_alarm_summary(
    session: AsyncSession,
    equipment_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    alarm_code: str | None = None,
) -> list[AlarmSummaryItem] | None:
    # 장비별 알람 코드 집계 요약 (NEW_ALARM01_HISTORY02)
    # 설비 존재 확인, 미존재면 None(라우터에서 404), 알람 없으면 빈 목록
    if not await repository.fetch_equipment_metadata(session, equipment_id=equipment_id):
        return None
    rows = await repository.fetch_alarm_history(
        session,
        equipment_id=equipment_id,
        start_time=start,
        end_time=end,
        alarm_code=alarm_code,
    )
    return [
        AlarmSummaryItem(
            alarm_code=row["alarm_code"],
            severity=assess_status(row["alarm_code"]),
            count=row["count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        for row in rows
    ]


async def get_alarm_sensor_summary(
    session: AsyncSession,
    equipment_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[AlarmSensorSummaryItem] | None:
    # 센서 변수별 알람 발생 집계 (NEW_ALARM01_HISTORY02), 도넛 센서별 세그먼트
    # 설비 존재 확인, 미존재면 None(라우터에서 404), 알람 없으면 빈 목록
    if not await repository.fetch_equipment_metadata(session, equipment_id=equipment_id):
        return None
    rows = await repository.fetch_alarm_sensor_summary(
        session, equipment_id=equipment_id, start=start, end=end
    )
    return [
        AlarmSensorSummaryItem(
            metric=row["metric"],
            count=row["count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        for row in rows
    ]


async def clear_equipment_alarm(session: AsyncSession, equipment_id: str) -> EquipmentDetail | None:
    # 알람 래치 해제(현장 점검·수리 완료), 미존재 설비는 None(라우터 404)
    # 해제 후 갱신된 상세 반환, 이상 신호가 지속되면 다음 tick에 다시 래치됨
    if not await repository.clear_equipment_alarm(session, equipment_id):
        return None
    await session.commit()  # 쓰기 경로 명시적 commit (get_session 자동 커밋 없음)
    return await get_equipment_detail(session, equipment_id)
