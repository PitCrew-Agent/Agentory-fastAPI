"""?붿????몄쐢???붾젅硫뷀듃由?REST (NEW_TWIN01_SYNC01 ?곗씠??諛깆뿏??

?꾨줎??3D 酉곌? 二쇨린 ?대쭅 ?먮뒗 SSE濡?援щ룆
"""

from fastapi import APIRouter, Depends, HTTPException

from agentory.modules.auth.dependencies import require_user_types
from agentory.modules.auth.schemas import UserResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/equipment/status")
async def equipment_status(
    current_user: UserResponse = Depends(require_user_types("ADMIN", "FIELD_ENGINEER")),
) -> list[dict]:
    """Return latest equipment status."""
    # TODO(二쇳씗??: 理쒖떊 ?붾젅硫뷀듃由?湲곗? ?곹깭 ?먯젙 濡쒖쭅 援ы쁽
    raise HTTPException(status_code=501, detail="NEW_TWIN01_SYNC01 not implemented")
