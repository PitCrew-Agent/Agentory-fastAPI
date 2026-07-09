"""디지털 트윈용 텔레메트리 REST (NEW_TWIN01_SYNC01 / NEW_LOOP01_CHECK01)

프론트 3D 뷰가 전체 설비 상태를 주기 폴링, 설비 선택 시 상세(상태+체크리스트) 조회
상태 판정·체크리스트 구성은 service 계층이 담당
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/lines", response_model=list[LineItem])
async def lines(
    session: AsyncSession = Depends(get_session),
) -> list[LineItem]:
    # 라인 목록 + 라인별 설비 수 (NEW_TWIN01_SYNC01), 라인 선택 드롭다운용
    return await service.list_lines(session)


@router.get("/equipment/status", response_model=list[EquipmentStatusItem])
async def equipment_status(
    line: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[EquipmentStatusItem]:
    # 설비 최신 상태 목록 (NEW_TWIN01_SYNC01), 3D 뷰 색상 매핑용, line 지정 시 해당 라인만
    return await service.list_equipment_status(session, line_name=line)


@router.get("/equipment/{equipment_id}", response_model=EquipmentDetail)
async def equipment_detail(
    equipment_id: str,
    session: AsyncSession = Depends(get_session),
) -> EquipmentDetail:
    # 선택 설비 상태 + 조치 체크리스트 (NEW_LOOP01_CHECK01)
    detail = await service.get_equipment_detail(session, equipment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return detail


@router.get("/equipment/{equipment_id}/suggestions", response_model=EquipmentSuggestionsResponse)
async def equipment_suggestions(
    equipment_id: str,
    session: AsyncSession = Depends(get_session),
) -> EquipmentSuggestionsResponse:
    # 선택 설비의 현재 상태 기반 챗봇 추천 메시지 3개 (NEW_TWIN01_SUGGEST01), 장비 클릭 시 호출
    suggestions = await service.get_equipment_suggestions(session, equipment_id)
    if suggestions is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return EquipmentSuggestionsResponse(suggestions=suggestions)


@router.post("/equipment/{equipment_id}/clear-alarm", response_model=EquipmentDetail)
async def clear_alarm(
    equipment_id: str,
    session: AsyncSession = Depends(get_session),
) -> EquipmentDetail:
    # 알람 래치 해제(현장 점검·수리 완료 반영), 해제 후 갱신된 상세 반환 (NEW_LOOP01_LATCH01)
    detail = await service.clear_equipment_alarm(session, equipment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return detail


@router.get("/equipment/{equipment_id}/series", response_model=list[SensorPoint])
async def equipment_series(
    equipment_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[SensorPoint]:
    # 설비 센서 시계열 (NEW_TWIN01_SYNC01), 그래프 위젯용, 기간 미지정 시 최근 window
    series = await service.get_sensor_series(session, equipment_id, start=start, end=end)
    if series is None:
        raise HTTPException(status_code=404, detail=f"설비 없음: {equipment_id}")
    return series
