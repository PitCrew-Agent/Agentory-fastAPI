"""디지털 트윈용 텔레메트리 REST (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)

프론트 3D 뷰가 전체 설비 상태를 주기 폴링, 설비 선택 시 상세(상태+체크리스트) 조회
상태 판정·체크리스트 구성은 service 계층이 담당
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentory.core.db import get_session
from agentory.modules.telemetry import service
from agentory.modules.telemetry.schemas import (
    EquipmentDetail,
    EquipmentStatusItem,
    EquipmentSuggestionsResponse,
    LineItem,
    SensorPoint,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/lines", response_model=list[LineItem], summary="라인 목록 조회 (설비 수 포함)")
async def lines(
    session: AsyncSession = Depends(get_session),
) -> list[LineItem]:
    """라인 목록·라인별 설비 수"""
    return await service.list_lines(session)


@router.get(
    "/equipment/status",
    response_model=list[EquipmentStatusItem],
    summary="설비 최신 상태 목록",
)
async def equipment_status(
    line: str | None = Query(default=None, description="지정 시 해당 라인만", examples=["A-Line"]),
    session: AsyncSession = Depends(get_session),
) -> list[EquipmentStatusItem]:
    """전체 설비 최신 상태 (3D 뷰 색상 매핑)"""
    return await service.list_equipment_status(session, line_name=line)


@router.get(
    "/equipment/{equipment_id}",
    response_model=EquipmentDetail,
    summary="설비 상세 조회",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_detail(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> EquipmentDetail:
    """설비 상세 (상태·메타·센서·책임자·체크리스트)"""
    detail = await service.get_equipment_detail(session, equipment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return detail


@router.get(
    "/equipment/{equipment_id}/suggestions",
    response_model=EquipmentSuggestionsResponse,
    summary="설비 챗봇 추천 질문",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_suggestions(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> EquipmentSuggestionsResponse:
    """설비 상태 기반 챗봇 추천 질문 3개"""
    suggestions = await service.get_equipment_suggestions(session, equipment_id)
    if suggestions is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return EquipmentSuggestionsResponse(suggestions=suggestions)


@router.post(
    "/equipment/{equipment_id}/clear-alarm",
    response_model=EquipmentDetail,
    summary="설비 알람 래치 해제",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def clear_alarm(
    equipment_id: str = Path(examples=["EQP-A01"]),
    session: AsyncSession = Depends(get_session),
) -> EquipmentDetail:
    """알람 래치 해제 후 갱신 상세 (현장 점검·수리 반영)"""
    detail = await service.clear_equipment_alarm(session, equipment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return detail


@router.get(
    "/equipment/{equipment_id}/series",
    response_model=list[SensorPoint],
    summary="설비 센서 시계열 조회",
    responses={404: {"description": "설비가 존재하지 않음"}},
)
async def equipment_series(
    equipment_id: str = Path(examples=["EQP-A01"]),
    start: datetime | None = Query(default=None, examples=["2026-07-10T00:00:00+09:00"]),
    end: datetime | None = Query(default=None, examples=["2026-07-10T23:59:59+09:00"]),
    session: AsyncSession = Depends(get_session),
) -> list[SensorPoint]:
    """설비 센서 시계열 (기간 미지정 시 최근 구간)"""
    series = await service.get_sensor_series(session, equipment_id, start=start, end=end)
    if series is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return series
