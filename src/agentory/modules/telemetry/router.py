"""디지털 트윈용 텔레메트리 REST (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)

프론트 3D 뷰가 전체 설비 상태를 주기 폴링, 설비 선택 시 상세(상태+체크리스트) 조회
상태 판정·체크리스트 구성은 service 계층이 담당
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.common.exceptions import NotFoundError
from agentory.common.response import ApiResponse
from agentory.core.db import get_session
from agentory.modules.auth.audit import audit
from agentory.modules.telemetry import service
from agentory.modules.telemetry.schemas import (
    AlarmHistoryPage,
    AlarmSensorSummaryItem,
    AlarmSummaryItem,
    EquipmentDetail,
    EquipmentStatusItem,
    EquipmentSuggestionsResponse,
    LineItem,
    SensorPoint,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get(
    "/lines", response_model=ApiResponse[list[LineItem]], summary="라인 목록 조회 (설비 수 포함)"
)
async def lines(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[LineItem]]:
    """라인 목록·라인별 설비 수"""
    return ApiResponse.ok(await service.list_lines(session))


@router.get(
    "/equipment/status",
    response_model=ApiResponse[list[EquipmentStatusItem]],
    summary="설비 최신 상태 목록",
)
async def equipment_status(
    line: str | None = Query(default=None, description="지정 시 해당 라인만", examples=["A-Line"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[EquipmentStatusItem]]:
    """전체 설비 최신 상태 (3D 뷰 색상 매핑)"""
    return ApiResponse.ok(await service.list_equipment_status(session, line_name=line))


@router.get(
    "/equipment/{equipment_id}",
    response_model=ApiResponse[EquipmentDetail],
    summary="설비 상세 조회",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_detail(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[EquipmentDetail]:
    """설비 상세 (상태·메타·센서·책임자·체크리스트)"""
    detail = await service.get_equipment_detail(session, equipment_id)
    if detail is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(detail)


@router.get(
    "/equipment/{equipment_id}/suggestions",
    response_model=ApiResponse[EquipmentSuggestionsResponse],
    summary="설비 챗봇 추천 질문",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_suggestions(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[EquipmentSuggestionsResponse]:
    """설비 상태 기반 챗봇 추천 질문 3개"""
    suggestions = await service.get_equipment_suggestions(session, equipment_id)
    if suggestions is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(EquipmentSuggestionsResponse(suggestions=suggestions))


@router.post(
    "/equipment/{equipment_id}/clear-alarm",
    response_model=ApiResponse[EquipmentDetail],
    summary="설비 알람 래치 해제",
    responses={404: {"description": "설비가 존재하지 않음"}},
    dependencies=[Depends(audit("EQUIPMENT_CLEAR_ALARM"))],
)
async def clear_alarm(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[EquipmentDetail]:
    """알람 래치 해제 후 갱신 상세 (현장 점검·수리 반영)"""
    detail = await service.clear_equipment_alarm(session, equipment_id)
    if detail is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(detail)


@router.get(
    "/equipment/{equipment_id}/alarms",
    response_model=ApiResponse[AlarmHistoryPage],
    summary="설비 알람 이력 조회 (발생 이벤트 타임라인)",
    responses={
        400: {"description": "before 커서 형식이 잘못됨"},
        404: {"description": "설비가 존재하지 않음"},
    },
)
async def equipment_alarms(
    equipment_id: str = Path(examples=["EQP-A01"]),
    alarm_code: str | None = Query(
        default=None, description="지정 시 해당 코드만", examples=["ERR-402"]
    ),
    start: datetime | None = Query(default=None, examples=["2026-07-10T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-10T23:59:59+09:00"]),
    before: str | None = Query(
        default=None,
        description="이전 페이지 마지막 항목 커서, 첫 페이지는 생략",
        examples=["MjAyNi0wNy0xMFQxNTo0MzoyNSswOTowMHwxMDI0"],
    ),
    limit: int = Query(
        default=service.DEFAULT_ALARM_PAGE_SIZE,
        ge=1,
        le=service.MAX_ALARM_PAGE_SIZE,
        examples=[10],
    ),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[AlarmHistoryPage]:
    """설비 알람 발생 이력, 발생 역순 커서 페이지네이션(기본 10개)"""
    # 잘못된 커서는 서비스가 ValidationError raise, 전역 핸들러가 400 통일 응답
    page = await service.list_alarm_events(
        session,
        equipment_id,
        start=start,
        end=end,
        alarm_code=alarm_code,
        before=before,
        limit=limit,
    )
    if page is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(page)


@router.get(
    "/equipment/{equipment_id}/alarms/summary",
    response_model=ApiResponse[list[AlarmSummaryItem]],
    summary="설비 알람 코드별 집계 요약",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_alarm_summary(
    equipment_id: str = Path(examples=["EQP-A01"]),
    alarm_code: str | None = Query(
        default=None, description="지정 시 해당 코드만", examples=["ERR-402"]
    ),
    start: datetime | None = Query(default=None, examples=["2026-07-10T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-10T23:59:59+09:00"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[AlarmSummaryItem]]:
    """설비 알람 코드별 발생 횟수·최초/최근 시각 집계 (기간 미지정 시 전체)"""
    summary = await service.get_alarm_summary(
        session, equipment_id, start=start, end=end, alarm_code=alarm_code
    )
    if summary is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(summary)


@router.get(
    "/equipment/{equipment_id}/alarms/sensors",
    response_model=ApiResponse[list[AlarmSensorSummaryItem]],
    summary="설비 센서 변수별 알람 집계 (도넛)",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_alarm_sensor_summary(
    equipment_id: str = Path(examples=["EQP-A01"]),
    start: datetime | None = Query(default=None, examples=["2026-07-10T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-10T23:59:59+09:00"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[AlarmSensorSummaryItem]]:
    """설비 센서 변수별 알람 발생 횟수 집계 (도넛 차트용, 기간 미지정 시 전체)"""
    summary = await service.get_alarm_sensor_summary(session, equipment_id, start=start, end=end)
    if summary is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(summary)


@router.get(
    "/equipment/{equipment_id}/series",
    response_model=ApiResponse[list[SensorPoint]],
    summary="설비 센서 시계열 조회",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_series(
    equipment_id: str = Path(examples=["EQP-A01"]),
    start: datetime | None = Query(default=None, examples=["2026-07-10T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-10T23:59:59+09:00"]),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[SensorPoint]]:
    """설비 센서 시계열 (기간 미지정 시 최근 구간)"""
    series = await service.get_sensor_series(session, equipment_id, start=start, end=end)
    if series is None:
        raise NotFoundError("error.equipment.not_found", params={"id": equipment_id})
    return ApiResponse.ok(series)
