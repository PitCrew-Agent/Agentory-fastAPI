"""디지털 트윈용 텔레메트리 REST (NEW_TWIN01_SYNC01 데이터 백엔드)

프론트 3D 뷰가 주기 폴링 또는 SSE로 구독
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/equipment/status")
async def equipment_status() -> list[dict]:
    """설비별 최신 상태(정상/경고/이상) 목록, 3D 뷰 색상 매핑용"""
    # TODO(주희정): 최신 텔레메트리 기준 상태 판정 로직 구현
    raise HTTPException(status_code=501, detail="NEW_TWIN01_SYNC01 미구현")
